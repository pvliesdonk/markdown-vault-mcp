# Repository Protection

The template ships GitHub repository rulesets that gate the branches a
release can ship from. The rules live as JSON files in `.github/rulesets/`
and the `bootstrap.yml` workflow applies them, so the effective protection
state is checked in, reviewed, and re-applied rather than clicked together
in the UI and forgotten.

## What ships

Three rulesets, one file each:

| Ruleset | Targets | Rules |
|---|---|---|
| `protect-main` | the default branch | pull request required (zero approvals), `CI Success` status check (strict), no deletion, no force push |
| `protect-release-branches` | `release/*` branches | same gates as the default branch |
| `protect-release-tags` | `v*` tags | no deletion, no force move (creation stays open) |

The reasoning per branch class:

- **`main`** requires a pull request and the aggregate `CI Success` check.
  Zero required approvals is deliberate: a solo-maintainer account has no
  second reviewer, and requiring one would deadlock the Renovate
  patch/minor auto-merge. The pull request requirement still routes every
  change through CI and the review tooling; the check requirement is what
  holds an auto-merge until CI is green.
- **`release/*`** stabilisation branches carry a shipped release for their
  lifetime, so backports get the same gate class as `main`: pull request
  plus `CI Success`, with the CI structural gate measuring the diff against
  the release branch itself rather than `main`. The generated `ci.yml` runs
  on pull requests targeting `release/*` for exactly this reason. Status
  checks are not enforced on branch creation, so cutting `release/X.Y` is a
  plain push.
- **`v*` tags** are the identity of every shipped release: package
  registries, Docker tags, and install instructions all point at them.
  The ruleset blocks deleting or force-moving them. Creating tags stays
  unrestricted because the release workflow creates one per release.

## Who bypasses, and how

Ruleset bypass lists name roles, teams, apps, or deploy keys, not user
accounts. The shipped entry grants bypass to the **repository admin role**
(`actor_id` 5, the one actor a template can name without knowing your
account), with `bypass_mode: always`.

The release flow depends on this. `RELEASE_TOKEN` is a personal access
token, and a personal access token (classic or fine-grained) acts as its
owner and holds the repository role of that owner. The release-PR flow
shrank what the token does: version bumps reach `main` through reviewed
pull requests now, so no release commit or merge-back is ever pushed
directly to a protected branch. Its remaining operations still rely on
the admin bypass:

- the `vX.Y.Z` tag and GitHub release that knope creates after a release
  pull request merges (tag creation is open, but the token must satisfy
  the `v*` tag ruleset's other rules),
- the owner's own hotfix pushes, preserving the escape hatch the previous
  classic protection provided via its disabled admin enforcement.

The token's other release-flow duties (pushing the `knope/prepare/*`
preparation branches, and opening the release, notes, and port
pull requests) need no bypass at all. They use a personal token rather
than `GITHUB_TOKEN` only so the resulting pull requests trigger CI, which
a `GITHUB_TOKEN`-created pull request never does.

Two clean-up operations the release model implies depend on this bypass
too. `protect-release-branches` blocks deleting a `release/X.Y` branch, and
`protect-release-tags` blocks deleting a `v*` tag, yet a stabilisation
branch is short-lived, and the `Pre-release check` workflow describes its
`v<version>-rc` pre-release as safe to delete. Both deletions are available
only to the admin role, through the same bypass. A `RELEASE_TOKEN` that is
not owned by a repository admin cannot perform them, and those spent
branches and pre-release tags then accumulate until an admin removes them.

Everyone below admin, including outside collaborators, write-role
contributors, and any workflow using the default `GITHUB_TOKEN`, goes
through a pull request with green CI.

If your release credential is a GitHub App installation token rather than
a personal access token, the role-based entry does not cover it: add a
bypass actor with `actor_type: Integration` and the app id to each
ruleset file.

## The files own the state

`bootstrap.yml` upserts each ruleset by name: a ruleset whose name
matches is replaced wholesale with the checked-in state, and a missing one
is created. Edits made by hand in the GitHub UI are reset on the next run,
so change the JSON files instead. A
push that touches `.github/rulesets/` or the bootstrap workflow re-applies
automatically; `workflow_dispatch` re-applies on demand. Rulesets with
other names are never touched, so you can add your own alongside.

The apply step needs `administration: write`, a permission the default
`GITHUB_TOKEN` cannot be granted, so it runs with `RELEASE_TOKEN`,
matching the permission set the README already asks for.

## Requiring a check the template does not own

`CI Success` is the only context the shipped rulesets require, and it is an
aggregate: it passes when every job in the generated `ci.yml` passed, so
adding a job there needs no ruleset change. A check that lives outside that
workflow is a different matter. A workflow cannot list another workflow's
job in its own `needs:`, so a domain workflow the project added itself
cannot join the aggregate: it runs, it shows red on the pull request, and
nothing stops the merge.

List its context in the `extra_required_checks` copier answer instead:

```yaml
# .copier-answers.yml
extra_required_checks:
  - SPA sources
```

Each name renders into the `required_status_checks` array of both branch
rulesets, alongside `CI Success`, and applies to `main` and `release/*`
alike. The answer is the project's own, so the rulesets stay template-owned
and a `copier update` re-renders them with the project's checks intact. An
empty answer, the default, renders exactly the single-context form every
project already had.

Write each name as the check appears on the pull request. That is the job's
`name:` when it has one and the job id otherwise, not the workflow's name,
and not the file it lives in.

The rendered files reach GitHub the same way as any other ruleset change:
commit them and push, and the `.github/rulesets/` path filter starts
`bootstrap.yml`; a `workflow_dispatch` run applies them on demand.

!!! warning "A required check must report on every pull request"
    A required context that never reports blocks the merge forever. The
    pull request waits for a check that is not coming, and there is no
    timeout. This is easy to trigger by accident, because the natural way
    to write a domain workflow is to scope it:

    - a `paths:` filter means the check reports on the pull requests that
      touch those files and no others,
    - a job-level `if:` that evaluates false skips the job, which reports
      nothing,
    - a workflow that runs only on `push` never reports on a pull request
      at all.

    Let the workflow run on every pull request to a protected branch and
    decide inside the job whether there is work to do, exiting zero when
    there is not. Verify on a pull request that touches nothing the check
    cares about: the context must still appear, and pass.

### `codecov/patch`, if you require it

`codecov/patch` is the one context this rule applies to that the template
itself ships, and it is worth knowing how it reaches a pull request before
you add it to `extra_required_checks`.

Two workflows post it. `ci.yml` posts it directly for a pull request from a
branch in this repository. A pull request from a fork gets a read-only token,
so `ci.yml` cannot write the status there; `coverage-status.yml` posts it
instead, from a `workflow_run` that executes in this repository's context
after CI finishes.

Both post under every outcome, including an `error` state when the coverage
result is missing. That is deliberate: an `error` is recoverable, because a
maintainer can re-run the workflow, while a missing status is not. If you
require this context and a fork pull request stalls on it, check the **Post
Coverage Status** workflow's runs rather than the CI run: the status comes
from there.

## Applying by hand

Without the bootstrap workflow (or when importing into another repository):

```bash
gh api -X POST "repos/OWNER/REPO/rulesets" \
  --input .github/rulesets/protect-main.json
```

or import each file in the UI under **Settings → Rules → Rulesets →
New ruleset → Import a ruleset**. The JSON files use the same schema the
UI exports.

## Plan limits

GitHub enforces rulesets on public repositories on every plan; private
repositories need a paid plan. On a Free-plan private repository the
ruleset API rejects the apply and the `settings` job fails; the labels
job still runs, and the rulesets take effect once the repository is public
or the plan allows enforcement.
