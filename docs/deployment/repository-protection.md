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
owner and holds the repository role of that owner. With the token owned by
a repository admin, all of the release pipeline's direct pushes bypass the
rules:

- the release commit and `vX.Y.Z` tag that python-semantic-release pushes
  to the released branch (`main` or `release/X.Y`),
- the merge-back merge commit pushed to `main` after a release cut from a
  `release/*` branch,
- the owner's own hotfix pushes, preserving the escape hatch the previous
  classic protection provided via its disabled admin enforcement.

Everyone below admin, including outside collaborators, write-role
contributors, and any workflow using the default `GITHUB_TOKEN`, goes
through a pull request with green CI.

If your release credential is a GitHub App installation token rather than
a personal access token, the role-based entry does not cover it: add a
bypass actor with `actor_type: Integration` and the app id to each
ruleset file.

## The files own the state

`bootstrap.yml` upserts each ruleset by name: it updates the existing
ruleset when one matches and creates it otherwise, replacing the whole
ruleset with the checked-in state every time. Edits made by hand in the
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
