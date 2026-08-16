# Release Process

`main` is trunk: every change merges there, and merging is not releasing.
A merge feeds the rolling `edge` channel and nothing else. A release is a
separate, deliberate event, cut by dispatching the **Release** workflow,
and this page describes what each kind of release publishes and where
releases come from.

## Channels

| Channel | Version identity | What it promises |
|---|---|---|
| `edge` | None; the commit is the identity | The newest merged code. Every merge to `main` rebuilds the rolling Docker tag plus an `.mcpb` bundle workflow artifact, and the rolling `unstable` docs version deploys from the same trigger. It leaves no git tag, GitHub release, or PyPI entry behind. |
| Pre-release | `vX.Y.Z-rc.N`, fixed at cut time | A stabilisation step toward exactly that version, cut from a `release/X.Y` branch. Publishes a GitHub release with wheels, `sdist`, `.mcpb` bundle, and SBOM attached, plus a Docker image under its immutable version tag. Skips PyPI, `.deb`/`.rpm` packages, the marketplace and registry entries, and the docs deploy. |
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
`release/X.Y` branch never moves them back to older content. See
[Image tags](docker.md#image-tags) for the Docker tag list.

## Releasing from trunk

The default release path is trunk. When trunk is quiescent (no epic that
must ship whole is mid-flight), dispatching Release on `main` cuts a
stable from the head commit: no branch, no ceremony. The workflow prints
an advisory warning when open `ships-atomically` epics exist, because
releasing `main` ships everything merged so far; the warning never
blocks, since the cut may still be intentional.

## Stabilisation branches

A short-lived `release/X.Y` branch is the exception tool, for two cases:

1. **Trunk carries unfinished work** that the release must exclude. The
   branch is cut from the last quiescent commit behind the head, release candidates are
   cut there while fixes land, and the `finalize` dispatch input cuts the
   final stable `X.Y.Z`.
2. **A shipped release needs a patch** while `main` has moved on. The
   branch can be created retroactively from the release tag, so no branch
   needs to exist in advance of the need.

Fixes flow from trunk to the branch: they land on `main` first and are
cherry-picked over. After any release cut from a `release/X.Y` branch, an
automated merge-back job merges the branch into `main`; that merge is the
single reverse flow, and it carries only the release machinery's version
commits, never features.

Release branches carry shipped releases, so they get the same protection
as `main`: pull requests plus green CI, applied by the shipped rulesets.
See [Repository Protection](repository-protection.md).

## Where to read about a release

Each release is described in three places with distinct jobs:

- **The GitHub release body** carries a short user-facing summary and a
  link to the release's notes page on this site. The body is a pointer,
  not the record.
- **The release notes pages on this docs site** are the canonical
  human-facing narrative of what changed and why it matters.
- **`CHANGELOG.md`** in the repository is the machine-written,
  commit-level audit trail, generated from conventional commits by the
  release tooling.

### How the notes pages are produced

The pages under [Release Notes](../releases/index.md) cover one minor
series each; a patch release adds a dated section to its series page.
After every stable release, the **Release Notes** workflow drafts the
page: an agent researches the release range through the GitHub API (the
linked issues and pull requests, not commit subjects) and opens a pull
request against `main`. Every causal claim in a page must cite a linked
issue or pull request, and the draft must pass the same prose lint as the
rest of this site before the pull request opens.

A maintainer merge is the publication step. Nothing lands on the site
without review, and until the merge the release keeps the short interim
body the release workflow wrote; a failed or unconvincing draft never
blocks or alters a release. On merge, the **Release Notes Publish**
workflow updates the GitHub release body from the page's summary and
redeploys that minor's versioned docs so the body's deep link resolves.
Later hand edits to a merged page redeploy the docs the same way.
