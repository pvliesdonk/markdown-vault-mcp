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

`bootstrap.yml` upserts each ruleset by name: it updates the existing
ruleset when one matches and creates it otherwise. Every apply replaces
the whole ruleset with the checked-in state. Edits made by hand in the
GitHub UI are reset on the next run, so change the JSON files instead. A
push that touches `.github/rulesets/` or the bootstrap workflow re-applies
automatically; `workflow_dispatch` re-applies on demand. Rulesets with
other names are never touched, so you can add your own alongside.

The apply step needs `administration: write`, a permission the default
`GITHUB_TOKEN` cannot be granted, so it runs with `RELEASE_TOKEN`,
matching the permission set the README already asks for.

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
