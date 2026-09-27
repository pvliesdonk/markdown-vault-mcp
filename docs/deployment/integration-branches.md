# Integration Branches

An integration branch parks an epic's pull requests until the whole epic is
done, then brings it to `main` in one pull request. It is optional. Most work
merges straight to `main`, and an epic whose children can ship one at a time
needs no integration branch at all.

Reach for one when an epic spans many pull requests that must ship together,
while unrelated fixes keep landing on `main`. Without it, the release model
in the `releasing` skill treats an open atomic epic as a dirty trunk: a
release waits, or needs a `release/X.Y` branch cut from before the epic
started. With the epic parked on `integration/<epic>`, trunk stays releasable
throughout.

## What the template provides

- `integration/*` branches are protected: child pull requests need a green
  `CI Success`, and force pushes are blocked. Checks a project adds through
  `extra_required_checks` are required only on the final pull request to
  `main`. See
  [Repository Protection](repository-protection.md).
- `ci.yml`, `docs.yml`, `codeql.yml` and the automatic review run on pull
  requests that target an `integration/*` branch, and CI also runs on every
  push to one.
- The local structural and template-conformance gates measure a child branch
  against the integration branch it was cut from, not against `main`.
- The release workflows ignore integration branches. Nothing is released
  from one, and merging a child pull request releases nothing.

## Running an epic on an integration branch

1. **Create the branch** from `main` and push it. Status checks are not
   enforced on branch creation.

    ```bash
    git switch -c integration/<epic> origin/main
    git push -u origin integration/<epic>
    ```

2. **Open the final pull request early**, as a draft from
   `integration/<epic>` to `main`. Its CI runs against `main` on every push,
   so a conflict or a broken build shows up while it is small, and its diff
   is the epic's running total.

3. **Target each child pull request at the integration branch** and squash
   merge it, as you would into `main`. The pull request title still has to
   be a conventional commit: the squash commit carries it to `main` later,
   where the changelog reads it.

    Write `Part of #<epic>` or `Refs #N` in a child's body, not `Closes #N`.
    GitHub closes linked issues only when a pull request merges into the
    default branch, so a closing keyword in a child does nothing. Collect the
    `Closes` lines in the final pull request instead.

4. **Merge `main` in; never rebase.** Bring `main` into the integration
   branch after each release and whenever a conflict appears:

    ```bash
    git switch integration/<epic>
    git pull --ff-only
    git merge origin/main
    git push
    ```

    A rebase rewrites every child commit already on the branch, so each open
    child pull request would suddenly show its siblings' changes as its own,
    and the reviewed commits would no longer exist. A merge keeps them, and
    it records any conflict resolution where a reviewer can find it. Pushing
    the merge directly relies on the ruleset bypass that the repository
    admin role holds; anyone else opens a pull request from `main` into the integration branch
    and merges it with a merge commit.

5. **Finish with a merge commit.** Before merging the final pull request,
   check that no child pull request is still open: once the base branch is
   deleted, GitHub retargets open children to `main`. Then merge with
   **Create a merge commit**, never with squash.

    knope computes the next version and the changelog from every commit
    reachable from `main` since the last release, including the ones a merge
    commit brings in, and counts each commit once. With a merge commit, the
    changelog keeps one entry per child, and any child's `!` still decides
    the version bump. A squash would collapse the whole epic into the final
    pull request's single title. It also keeps the project's rule that
    roughly every issue and pull request maps to one commit on `main`.

6. **Delete the branch** through GitHub, or let automatic head-branch
   deletion do it. Deleting it with `git push --delete` while a child pull
   request is still open closes that pull request instead of retargeting
   it.

## Reviewing the final pull request

Every child was reviewed when it merged into the integration branch. The
final review covers what those reviews could not see:

- **Conflict resolutions.** Each merge of `main` into the branch may have
  resolved conflicts by hand. Git 2.36 and later shows exactly those
  resolutions, and nothing else:

    ```bash
    git log --remerge-diff --merges origin/main..origin/integration/<epic>
    ```

- **Semantic conflicts.** Changes on `main` that merged cleanly but break an
  assumption the epic relies on. The final pull request's CI is the
  mechanical check; the review is the other.
- **The epic as a whole.** Whether the children fit together, and whether
  docs, configuration and migration notes describe the finished result
  rather than an intermediate step.
- **Release impact.** The breaking markers among the children, and the
  `Closes` lines in the final pull request's body.

The automatic review, where enabled, sees the whole diff against `main`. Its
findings on code a child review already accepted can be dismissed with a
pointer to that child pull request.

If the branch was rebased after all, the reviewed commits no longer exist
and GitHub's "changes since last review" view cannot be relied on. Compare the old series
with the new one instead, using the branch tip from before the rebase:

```bash
git range-diff <old-base>..<old-tip> origin/main..origin/integration/<epic>
```

It pairs each old commit with its rewritten form and shows only the ones
whose content changed, which is what needs a fresh look.
