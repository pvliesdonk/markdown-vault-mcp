# Release Process

`main` is trunk: every change merges there, and merging a feature is not
releasing. A merge feeds the rolling `edge` channel and nothing else. A
release is a separate, deliberate event: a maintainer dispatches the
**Release Prepare** workflow, reviews the release pull request it opens,
and merges it. The merge is what tags and publishes. This page describes
that flow, what each kind of release publishes, and where releases come
from.

## Cutting a release, step by step

The checklist a maintainer follows; each step links the section that
explains it.

1. **Draft the release notes.** Invoke the `writing-release-notes` skill
   with your coding agent (it lives at
   `.agents/skills/writing-release-notes/SKILL.md`). It researches the
   range through the GitHub API, writes `docs/releases/next.md`, runs
   Vale and a strict MkDocs build, and opens an ordinary pull request.
   Review and merge that pull request first; Release Prepare refuses to
   run when reviewed notes are missing. When user-visible behaviour lands
   after a first release candidate, invoke the skill's
   `refresh-known-target` mode and merge its pull request before the next
   dispatch. See [How the notes pages are produced](#how-the-notes-pages-are-produced).
2. **Dispatch Release Prepare.** In the Actions tab, run **Release
   Prepare** on `main` (or on a `release/X.Y` branch for a stabilisation
   release). Leave `channel` on `auto` unless you are promoting a
   candidate to stable. See [The release pull request](#the-release-pull-request).
3. **Review the release pull request.** Check the computed version against
   the breaking-change policy in `AGENTS.md`, read the changelog section,
   confirm the promoted notes page is the one you merged in step 1, and
   compare its `notes-range-end` with the release delta so the notes cover
   every commit being released. Never press GitHub's "Update branch" on
   it; dispatch Release Prepare again instead.
4. **Merge it.** The merge is the release: the **Release** workflow tags
   the commit, creates the GitHub release and publishes every channel.
5. **Verify the fan-out.** Each publish runs as its own job in the Release
   workflow: `publish-pypi`, `publish-docker`, `publish-linux-packages` and
   `publish-mcpb` on every release; `publish-plugin-zip` when the project
   ships the Claude Code plugin channel; `publish-claude-plugin` and
   `publish-registry` only for a stable release that is the newest one
   (a release candidate, or a patch to an older series, skips both by
   design). The documentation site deploys for the new version. A failed
   publish job can be re-run from the workflow run; the tag and release
   already exist, and every job derives from the reviewed version string,
   so a re-run publishes the same artefacts.
6. **If the release was cut from a `release/X.Y` branch**, expect the
   automated pull request that ports the changelog section back to
   `main`, and merge it. See [Stabilisation branches](#stabilisation-branches).

## Channels

| Channel | Version identity | What it promises |
|---|---|---|
| `edge` | None; the commit is the identity | The newest merged code. Every merge to `main` rebuilds the rolling Docker tag plus an `.mcpb` bundle and a Claude Code plugin `.zip` as workflow artifacts, and the rolling `unstable` docs version deploys from the same trigger. It leaves no git tag, GitHub release, or PyPI entry behind. |
| Pre-release | `vX.Y.Z-rc.N`, computed and reviewed in its release pull request | A stabilisation step toward exactly that version, normally cut from a `release/X.Y` branch. Publishes a GitHub release with wheels, `sdist`, `.deb`/`.rpm` packages, `.mcpb` bundle, plugin `.zip`, and SBOM attached, plus the wheel on PyPI and a Docker image under its immutable version tag and the rolling `rc` tag. Skips the marketplace and registry entries and the docs deploy. |
| Stable | `vX.Y.Z` | The full artifact set: PyPI, Docker, Linux packages, GitHub release assets (wheels, `sdist`, `.mcpb` bundle, SBOM), marketplace and registry entries, versioned docs. |

Pre-releases do reach PyPI, and that does not put them in front of
ordinary installers. A PEP 440 resolver skips pre-releases unless the
requirement pins one or you pass `--pre`, so `pip install markdown-vault-mcp`
and `uv add markdown-vault-mcp` still resolve to the newest stable. Ask for a
candidate by name to get one:

```bash
pip install markdown-vault-mcp==X.Y.ZrcN
```

Note the spelling. Tags and the changelog use SemVer (`vX.Y.Z-rc.N`);
PyPI uses the PEP 440 canonical form (`X.Y.ZrcN`).

Candidates need PyPI because the `.mcpb` bundle points there rather than
carrying the code. Its manifest launches `uvx --from markdown-vault-mcp[all]==<version>`,
so a candidate absent from PyPI ships a bundle that fails on the tester's
machine at install time. `edge` still publishes nothing to PyPI: it has no
version identity to publish under.

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

## Testing a candidate's Claude Code plugin

Every release attaches `markdown-vault-mcp-plugin-<version>.zip`, and every
merge to `main` produces the same archive as the `plugin-zip-edge` workflow
artifact. Load one without installing anything:

```bash
claude --plugin-url https://github.com/pvliesdonk/markdown-vault-mcp/releases/download/vX.Y.Z-rc.N/markdown-vault-mcp-plugin-X.Y.Z-rc.N.zip
```

The plugin loads for that session only and leaves no install record, so this
is the way to try a candidate without disturbing the installed copy. Check it
came up with `/plugin`, or from the shell:

```bash
claude --plugin-url <url> plugin details markdown-vault-mcp
```

The zip carries its own wheel and launches it from `${CLAUDE_PLUGIN_ROOT}`,
so it works at versions no index serves: every candidate, and the `edge`
build, whose constant `0.0.0-dev` could never be published. Its
dependencies still resolve normally at launch, so the machine needs network
access the first time the server starts.

The marketplace entry is the other half of the channel and works
differently: it is a thin pointer that installs `markdown-vault-mcp` from PyPI,
it is what `/plugin install` reads, and it follows stable releases only.
Claude Code has no marketplace-free way to install a plugin permanently, so
a candidate is something you load per session rather than install.

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

<!-- TEMPLATE-TRACKING-START -->
## Template updates

Releases of this project and updates from its template are separate
events; the weekly template update pull request is covered in
[Working Through a Template Update](template-updates.md).
<!-- TEMPLATE-TRACKING-END -->

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

The pages under [Release Notes](../releases/index.md) cover one minor series
each; patch releases add undated version headings to their series page. Humans
prepare the narrative before release automation starts, as steps 1 to 4 of
[Cutting a release, step by step](#cutting-a-release-step-by-step) describe.

The first release candidate for a stable identity consumes `next.md` into the
canonical minor page. Later candidates and stable promotion reuse the same
`vX.Y.Z` entry. When user-visible behavior lands after the first candidate,
invoke `refresh-known-target` and merge the normal notes pull request before
re-dispatching Release Prepare. Corrections and redrafts use the same ordinary
pull-request path for shipped pages.

No notes bypass exists. Missing or ambiguous staging makes deterministic
promotion refuse the prepare. Every causal claim must cite a linked issue or
pull request, and the skill runs Vale and strict MkDocs before opening its pull
request. Later canonical-page edits redeploy that minor through the
deterministic **Release Notes Publish** workflow.
