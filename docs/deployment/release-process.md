# Release Process

`main` is trunk: every change merges there, and merging a feature is not
releasing. A merge feeds the rolling `edge` channel and nothing else. A
release is a separate, deliberate event: a maintainer dispatches the
**Release Prepare** workflow, reviews the release pull request it opens,
and merges it. The merge is what tags and publishes. This page describes
that flow, what each kind of release publishes, and where releases come
from.

## Channels

| Channel | Version identity | What it promises |
|---|---|---|
| `edge` | None; the commit is the identity | The newest merged code. Every merge to `main` rebuilds the rolling Docker tag plus an `.mcpb` bundle workflow artifact, and the rolling `unstable` docs version deploys from the same trigger. It leaves no git tag, GitHub release, or PyPI entry behind. |
| Pre-release | `vX.Y.Z-rc.N`, computed and reviewed in its release pull request | A stabilisation step toward exactly that version, normally cut from a `release/X.Y` branch. Publishes a GitHub release with wheels, `sdist`, `.deb`/`.rpm` packages, `.mcpb` bundle, and SBOM attached, plus a Docker image under its immutable version tag and the rolling `rc` tag. Skips PyPI, the marketplace and registry entries, and the docs deploy. |
| Stable | `vX.Y.Z` | The full artifact set: PyPI, Docker, Linux packages, GitHub release assets (wheels, `sdist`, `.mcpb` bundle, SBOM), marketplace and registry entries, versioned docs. |

Pre-release and `edge` builds never reach PyPI: PyPI is where every
ordinary installer looks, and unstable builds do not belong in front of
it. A pre-release's wheels are still attached to its
[GitHub release](https://github.com/pvliesdonk/markdown-vault-mcp/releases)
and installable by URL for anyone who opts in.

Rolling pointers are ordering-aware. The Docker `latest`, `vX`, and `vX.Y`
tags, the GitHub latest-release pointer, the docs `latest` alias, and the
marketplace and registry entries follow a release only when it is the
newest in the relevant series, so a patch release cut from an old
`release/X.Y` branch never moves them back to older content. The Docker
`rc` tag follows the same rule on the pre-release side: it moves only
while the candidate's version is still ahead of the newest stable. This holds
even when two releases overlap: each rolling channel checks the tag
ordering again inside its own publish job. See
[Image tags](docker.md#image-tags) for the Docker tag list.

## The release pull request

Dispatching **Release Prepare** on the branch to release from has the
release tool, [knope](https://knope.tech/), compute the next version from
the conventional commits since the last release in that branch's history.
It stamps the version-coupled files (`pyproject.toml` and `CHANGELOG.md`
natively; `uv.lock`'s self-version entry on every prepare and the
install-channel manifests on stable prepares, both through
`scripts/stamp_manifests.py`) and opens a release pull request against
the dispatched branch. The dispatch's `channel` input
picks the release kind: `auto` prepares a release candidate on
`release/X.Y` branches and a stable elsewhere, and an explicit `rc` or
`stable` choice overrides that. The optional `override_version` input
replaces the computed version with an explicit one, for the rare range
whose commits the tool counts nothing in; the workflow refuses a
dispatch on any branch other than the default branch or `release/X.Y`,
and refuses a computed version whose tag already exists.

The release pull request is an ordinary pull request: full CI runs on it,
and the reviewer checks the computed version against the breaking-change
policy and reads the changelog section in the diff. Merging it is the
release decision. On merge, the **Release** workflow tags the merge
commit and creates the GitHub release, then hands off to the publish
fan-out; every publish gate derives from the reviewed version string.

Two rules keep the flow sound:

- Never press GitHub's "Update branch" button on a release pull request.
  If the base branch moves while the pull request is open, dispatch
  Release Prepare again: it recreates the preparation branch from the
  base and refreshes the same pull request in place.
- A release candidate promotes through a plain `channel: stable` dispatch
  over the same commits. A guard verifies that nothing but release stamps
  and release-notes pages changed since the last candidate, first when
  the promotion is prepared (a drifted promotion refuses before its pull
  request even opens) and again before any tag is created; any other
  change forces a new candidate instead of a silently different stable.

## Releasing from trunk

The default release path is trunk. When trunk is quiescent (no epic that
must ship whole is mid-flight), dispatch Release Prepare on `main` and
merge the release pull request: no branch, no ceremony. The prepare
workflow prints an advisory warning when a release-named milestone still
has open issues, or when an open `ships-atomically` epic shows work in
flight. It counts the epic's native sub-issues, which may live in another
repository, so a cross-repo epic stays visible. Either way the warning
never blocks, since the cut may still be intentional.

## Stabilisation branches

A short-lived `release/X.Y` branch is the exception tool, for two cases:

1. **Trunk carries unfinished work** that the release must exclude. The
   branch is cut from the last quiescent commit behind the head, release
   candidates are cut there while fixes land, and a plain `channel:
   stable` dispatch promotes the final `X.Y.Z`.
2. **A shipped release needs a patch** while `main` has moved on. The
   branch can be created retroactively from the release tag, so no branch
   needs to exist in advance of the need.

Fixes flow from trunk to the branch: they land on `main` first and are
cherry-picked over. After a stable release cut from a `release/X.Y`
branch, an automated job opens an ordinary pull request that carries the
release's changelog section back to `main`, reviewed and CI-gated like
any other change, with no direct pushes to protected branches. Release
candidates port nothing: the stable's changelog section covers the whole
cycle.

Release branches carry shipped releases, so they get the same protection
as `main`: pull requests plus green CI, applied by the shipped rulesets.
See [Repository Protection](repository-protection.md).

## Where to read about a release

Each release is described in three places with distinct jobs:

- **The GitHub release body** carries the release's notes summary, its
  machine-written changelog section, and pointers: the versioned docs,
  the compare view, and a deep link to the notes page.
- **The release notes pages on this docs site** are the canonical
  human-facing narrative of what changed and why it matters.
- **`CHANGELOG.md`** in the repository is the machine-written audit
  trail, generated from conventional commits into each release pull
  request.

### How the notes pages are produced

The pages under [Release Notes](../releases/index.md) cover one minor
series each; a patch release adds a dated section to its series page.
The page is part of every release pull request, exactly like the
changelog. The Release Prepare workflow's notes job drafts or refreshes
it. The drafting agent reads the release range through the GitHub API,
working from the linked issues and pull requests rather than commit
subjects, and the finished page is committed onto the release pull
request's branch. Reviewing the release pull request covers the notes. Merging it lands the page in
the release's own tag, and the published docs and the release body's
summary and deep link all read the page from there. Release candidates carry their notes
draft the same way, so a candidate is the full release artifact,
narrative included. Every causal claim in a page must cite a linked
issue or pull request, and the drafting agent runs the same prose lint
as the rest of this site.

The release pull request stays a draft until the notes job lands its
page, so a release without its notes can never merge by accident. A
drafting failure fails the prepare run visibly and leaves the pull
request in draft; re-run the notes job, or re-dispatch Release Prepare
with `skip_notes` to release without a notes refresh. For a release shipped that way, the **Release Notes**
workflow's manual dispatch drafts a standalone notes pull request as a
backfill. Later hand edits to a released page redeploy that minor's
versioned docs through the **Release Notes Publish** workflow.
