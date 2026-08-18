# Release system vision: the release is a pull request

- **Status:** proposal v4, 2026-08-18. v2 revised the vision per maintainer review (conventional commits retained, adopt-not-build, stable equals last rc); v3 folded in a hands-on comparison of knope and release-please and the notes-placement direction; v4 re-weighs that comparison per maintainer findings (rc.0 numbering is cosmetic; the manifest invariant is per-surface resolvability, not committed pins; changelog fidelity lives in the notes page) and recommends knope. Not yet adopted; no machinery changes in this document.
- **Relates to:** epic #1082 (supersedes its design iterations), epic #1054 (closed, superseded), #1055 (cut 4.0.0).
- **Scope:** the intended end-state flow only. How to migrate from the current PSR machinery — sequencing, issue breakdown, the fate of `v4.0.0-rc.1` — is deliberately out of scope and comes after this vision is agreed.

## 1. Why redesign, in one page

Two epics tried to fix releasing and both missed, in opposite directions.

**#1054** shipped a coherent model (trunk releases, `release/X.Y` stabilisation branches, `edge` channel, agent-written notes) on top of PSR — and PSR's identity flaw survived the overhaul: **the version is computed at publish time, after every review has already happened, outside any pull request**. The flaw is not conventional commits — it is *when and where* the computation lands. Everything the audits kept finding traces back to that one fact:

- An rc tag asserts a *prediction* of the stable it stabilises toward, and trunk motion keeps falsifying it — the `v3.2.0-rc.1…7` series ran five weeks and became mathematically unable to ever produce `3.2.0`. Nobody saw the computed version until the tag already existed.
- A stabilisation branch must be *named* `release/X.Y` before the tooling has ever computed `X.Y` — a wrong guess is detected at release time, when it is most expensive.
- Merge-back after every branch release is a hard requirement only because PSR's "already released" check is repo-global while its version computation is ancestry-scoped — a machinery-imposed deadlock, not a property of releasing.
- The release commit is pushed with an admin-bypass token and never passes through CI or review.
- Release notes can only be drafted *after* the stable ships (template#371) — the review happens when it can no longer change anything.

**#1082** correctly diagnosed that the flaw was upstream of implementation, then over-corrected: "owned, verifiable release transactions", per-destination convergence verification, resumable publication, a protected append-only ledger — 18 children, no architecture agreed, zero PRs landed. The lesson taken from it here: move the version computation into a pull request, adopt the tooling that already exists for exactly this, and stop. This use case is not special enough to deserve bespoke machinery.

## 2. Requirements and preferences

**Requirements** — the flow must provide:

1. **`edge` releases on `main`** — every merge builds and ships a rolling, versionless artifact. (Exists today; kept unchanged.)
2. **PR-style releasing** — a release is prepared as a pull request whose diff contains the version bump and the changelog section, reviewed before anything publishes. Merging it is the act of releasing.
3. **Release candidates** — versioned `vX.Y.Z-rc.N` pre-releases with a clean promotion path to the stable.

**Preferences** — strong defaults that bend before the requirements do, and before they force bespoke machinery:

- **Conventional-commit-driven versioning.** The version is computed from commit history, as today. The correction over PSR is placement, not authorship: the computation lands in the reviewed diff, with an explicit override for the exceptional case — override is the escape hatch, never the default.
- **Adopt maintained tooling; do not build.** Owned release machinery is a maintenance liability that two epics have now demonstrated. Where an existing tool covers the flow, the flow bends to the tool.
- **The stable release equals the last rc — same source, asserted.** Promotion re-releases the candidate's exact source tree: the stable's diff against the last rc contains version/changelog stamps and nothing else, and the promotion path verifies this mechanically. (Bit-for-bit identical *artifacts* are out of reach where the version is baked in — a PyPI wheel's metadata carries it — so same-source-asserted is the deliberate strictness level.)
- **Per-surface resolvability, not committed pins.** The requirement on the install-channel manifests (`server.json`, plugin manifests, mcpb) is that **every install surface resolves, at install time, to a version that actually exists on that surface**. rc versions never reach PyPI, the MCP registry, or the marketplace, so surfaces pinned to those move on stable only. *How* — committed stable-only stamps via a small stamp step, or publish-time rendering from a version-neutral template as the mcpb channel already does — is an implementation choice made per surface, not a property of the release tool.
- **The narrative lives in `docs/releases/`, not in the release PR — and it is the fidelity layer.** The AI-written notes are reviewed in their own PR against the docs page, exactly as the notes contract works today: issue/PR links, revert narration, and causal explanation belong there. The GitHub release body is the machine changelog plus a pointer to the versioned docs page. `CHANGELOG.md` is the plain machine audit trail and is held to no higher standard.
- **No new per-contributor process.** Feature PRs keep their current discipline (conventional title, linked issue). No changeset-file-per-PR or news-fragment obligations.

## 3. The model

> A release is a pull request. The release tool computes the version from conventional commits and prepares a release PR carrying the bump and changelog; the narrative is reviewed in a sibling docs PR; merging the release PR is the decision, and the release step tags and publishes.

```mermaid
flowchart LR
    subgraph trunk [main]
        M1((c1)) --> M2((c2)) --> M3((release<br/>commit))
    end
    M1 -- every merge --> EDGE["edge<br/>ghcr :edge + unstable docs"]
    M2 -- "dispatch release tool<br/>(version computed from commits)" --> PR["release PR<br/>version stamps + changelog"]
    M2 -. "notes agent" .-> NPR["notes PR<br/>docs/releases/X.Y.md"]
    PR -- "review: CI, version, changelog" --> PR
    PR -- merge --> M3
    NPR -- merge --> M3
    M3 -- "tag vX.Y.Z or vX.Y.Z-rc.N" --> PUB["publish fan-out<br/>PyPI, Docker, GH release (changelog + docs link), docs, registry"]
```

### The three channels, restated

| Channel | Identity | Promise |
|---|---|---|
| `edge` | none — the commit is the identity | newest merged code, rebuilt on every merge to `main`; rolling, disposable |
| rc | `vX.Y.Z-rc.N` — the target was **computed and reviewed in a merged PR** | an installable stabilisation step toward exactly that version |
| stable | `vX.Y.Z` | the promotion of the last rc's exact source, or a direct cut from quiescent trunk; rolling pointers follow it only when it is newest in its series |

The rc promise finally holds: the target is no longer an unreviewed prediction. If the computation lands on a version the maintainer disagrees with, that surfaces in the release PR's diff — before any tag exists — and is corrected there.

## 4. The flow

### 4.1 Prepare

A human dispatches the release tool on the branch to release from (`main` by default). Releasing stays a deliberate event — dispatch-driven, not push-driven, so the release PR appears when a release is wanted, not on every merge. The workflow derives the channel from the branch (`release/*` → rc, a few lines of glue) and the tool:

1. **Computes the version** from conventional commits since the last release in that branch's series (`!`/`BREAKING CHANGE` → major, `feat` → minor, `fix` → patch). On a stabilisation branch this yields `X.Y.Z-rc.N` instead (§4.4). An explicit version override exists for the exceptional case.
2. **Stamps the version-coupled files** in the release PR's diff: `pyproject.toml`, `CHANGELOG.md`, and `uv.lock`'s self-version natively; the install-channel manifests through a small stamp step invoked with the computed version, applying the per-surface resolvability rule (§2) — stable stamps them, rc leaves them.
3. **Renders the changelog section** from the same conventional commits — machine-written, never hand-maintained.
4. **Opens or refreshes the release PR** against the branch it was dispatched from.

In parallel, the **notes agent** drafts or extends `docs/releases/X.Y.md` (existing `writing-release-notes` skill, unchanged evidence contract) as a sibling PR to `main` — now draftable as soon as a release PR exists, rc phase included, which resolves template#371's "notes can only be reviewed after the stable ships". The advisory quiescence check (release milestone, `ships-atomically` label) runs at prepare time — still advisory, never blocking.

### 4.2 Review

The release PR is an ordinary PR. Full CI runs on it — **the released commit has passed CI**, which the PSR-pushed release commit never did. The reviewer checks the computed version against the breaking-change policy (the review is the correction point the current system lacks: a mis-typed `!` no longer silently mis-versions a release) and the changelog section. The notes PR is reviewed on its own clock against the evidence contract; it does not gate the release (unchanged from today's contract), and hand edits are safe there because the release tool never touches it.

If trunk moves while a release PR is open, its content is refreshed by re-dispatching; merging a stale release PR would ship commits the changelog does not describe, so an advisory staleness check comments when the base has advanced.

### 4.3 Merge = release

Merging the release PR is the decision; the tool's release step (triggered by the merge) tags the release commit `vX.Y.Z[-rc.N]` and creates the GitHub release. The body is the machine changelog section plus a pointer to the versioned docs notes page — appended by the same body-edit step the pipeline has today. Tag creation is the only remaining ruleset-sensitive operation, replacing today's direct release-commit and merge-back pushes to `main`.

The publish fan-out is unchanged, with every gate derived from the tag's version string (prerelease-ness is `-rc.` in the tag — itself reviewed, so gate and intent cannot desync):

- stable: PyPI (trusted publishing), Docker `vX.Y.Z` + ordering-gated `latest`/`vX`/`vX.Y`, linux packages, mcpb bundle, `mike deploy` + ordering-gated `latest` alias, marketplace and MCP-registry entries (ordering-gated);
- rc: Docker `vX.Y.Z-rc.N`, GH pre-release + mcpb bundle only.

### 4.4 Release candidates and promotion

An rc is a release PR prepared with the rc channel — derived from dispatching on a `release/X.Y` branch. Cut rcs when genuinely stabilising; quiescent-trunk stables need no rc and no branch (unchanged doctrine from design decision 23). The tool numbers the series from `rc.0` — accepted as cosmetic; PEP 440 normalizes either way.

**Promotion honors "the stable is the last rc," same-source-asserted — and it is the tool's plain path.** Dispatching the same prepare workflow on the branch *without* the rc channel computes exactly `X.Y.Z` from the same commits (verified hands-on: after `-rc.0` and `-rc.1`, a plain run yields `X.Y.Z`, not the next minor). No finalize flag, no footer-commit ceremony, no one-run generated config override. The publish path additionally verifies that nothing but release stamps changed since `vX.Y.Z-rc.N` and refuses otherwise — new source means a new rc, never a silently different stable. Notes were already drafted and reviewed during the rc cycle; promotion adds no new prose.

### 4.5 Stabilisation branches and backports

`release/X.Y` remains the exception tool for the same two cases (dirty trunk at cut time; patching a shipped release, with the branch created retroactively from the tag). Release PRs simply target the branch; the tag lands there. The branch-naming chicken-and-egg is defused: the computed version is visible in the release PR before any tag exists, so a misnamed branch is a rename, not a burned tag.

**Merge-back as deadlock prevention is abolished** — the version computation is ancestry-local, so a branch tag cannot wedge `main`. What `main` still wants from a branch release is bookkeeping: the changelog section and the notes-page delta (notes pages are canonical on `main` only). Those port to `main` as an ordinary automated PR — no admin bypass, no conflict-resolution script, and nothing deadlocks if it merges late.

### 4.6 `edge`

Unchanged, by design: push to `main` → `ghcr :edge` + `mcpb-bundle-edge` artifact + rolling `unstable` docs. No tag, no release, no version, no manifest churn. `edge` stays the sole rolling unstable tag; rcs ship only under immutable version tags.

## 5. Decisions

| # | Decision |
|---|---|
| D1 | A release is a pull request; merging it is the act of releasing. |
| D2 | The version is computed from conventional commits **inside the release PR**, where it is reviewed before any tag exists. An explicit override exists for the exceptional case; it is never the default. The version is never computed at publish time. |
| D3 | Channel (rc vs stable) is derived from the branch by the dispatch workflow (`release/*` → rc) and expressed in the version string inside the reviewed diff; every publish gate derives from the resulting tag, never from a dispatch-time flag. |
| D4 | The tag is created by the tool *after* merge, on the merged release commit. Direct pushes to protected branches are removed from the release path. |
| D5 | `edge` is untouched: versionless, rolling, one producer. |
| D6 | The AI narrative lives in `docs/releases/`, reviewed in its own PR (existing notes contract), draftable from the moment a release PR exists — rc phase included (resolves template#371). It is the **fidelity layer**: issue/PR links, revert narration, causal explanation. The GitHub release body is the machine changelog plus a pointer to the versioned docs page. The release PR carries no hand-written prose. |
| D7 | `CHANGELOG.md` stays machine-written from conventional commits by the release tool, rendered into the release PR diff. It is a plain audit trail; entry richness (links, reverts) is the notes page's job, not the changelog's. |
| D8 | Conventional commits and the PR-title gate are retained at full weight: they feed both the changelog and the version computation, with the release PR as the review point PSR never had. |
| D9 | The breaking-change policy (operator surface / public library interface, assessed against last stable) is unchanged; `!` markers drive the computed major bump and the release-PR reviewer verifies the result against the policy. |
| D10 | The stable is the promotion of the last rc's exact source — **same source, asserted**: promotion is the tool's plain (non-rc) run over the same commits, and the publish path verifies a stamps-only diff against the candidate, refusing if other commits intervened; new source requires a new rc. |
| D11 | Stabilisation branches remain the exception tool. Merge-back as deadlock prevention is abolished; branch releases port changelog/notes bookkeeping to `main` via an ordinary automated PR. |
| D12 | **Per-surface resolvability**: every install surface must resolve, at install time, to a version that exists on that surface. rc versions reach none of PyPI/registry/marketplace, so the manifests pinned to those move on stable only — via a small stamp step or per-surface publish-time rendering (as the mcpb channel already does); the choice is made per surface at implementation time. `uv.lock` tracks `pyproject.toml` and moves on every release. |
| D13 | The flow is implemented by adopting **knope**, with release-please as the evaluated fallback (§6). Owned machinery shrinks to the publish fan-out (which exists), a small manifest stamp step, and thin guards. The flow is tool-agnostic: if the adopted tool disappoints, the adopter is swapped, not the model. |
| D14 | Per-destination convergence verification, publication ledgers, and resumable-transaction machinery are explicitly rejected as over-engineering. Workflow runs, the release-contract tests, and `get_server_info` are the observability surface. |

## 6. Tooling: adopt, don't build

Both finalists were evaluated hands-on against this repo's real files (2026-08-18): knope 0.23.0 exercised end-to-end in a scratch repo; release-please 17.11.1 verified by driving the library's own strategy and updater classes. v3 of this document read the evidence as favoring release-please; the maintainer's review re-weighed the three findings that carried that verdict, and each dissolves:

- *knope starts rc series at rc.0* — cosmetic; at most a one-time interaction with the existing `v4.0.0-rc.1` tag, which can be yanked if needed (a migration detail, §9).
- *knope's `versioned_files` cannot express stable-only manifest stamping* — a non-finding once the requirement is stated at the right altitude (D12): the invariant is per-surface resolvability, and a small stamp step invoked with the computed version (knope Command step) or publish-time rendering satisfies it per surface. The stamp step is a slimmed, testable descendant of `scripts/bump_manifests.py`, freed from PSR's tool-less container.
- *knope's changelog loses links and reverts* — by design acceptable: `docs/releases/` is the fidelity layer (D6/D7); the changelog is a plain audit trail.

**Recommended: [knope](https://knope.tech).** With those costs dissolved, its verified strengths decide:

- **Identical bump rules and `v*` tags**, computed from squash subjects — this repo's PR-title regime, unchanged (verified: `fix`→patch, `feat`→minor, `!`→major).
- **The cleanest rc→promotion path of any tool evaluated**: `--prerelease-label rc` yields the series; a plain run afterwards yields exactly `X.Y.Z` from the same commits — promotion is the *absence of a flag*, with no finalize footer commit, no config flip, no per-branch config split (verified hands-on).
- **The release-PR recipe is knope's flagship workflow** (`PrepareRelease` → PR; merge → `Release` tags and creates the GitHub release), with `--override-version`, high-quality `--dry-run` plans usable as PR bodies, and local tagging that needs no token.
- **No regeneration fights**: preparation runs only when dispatched; nothing force-pushes over the release branch.
- One-time migration items, known and small: normalize `CHANGELOG.md` headings (`## vX.Y.Z` → `## X.Y.Z`; the `<!-- version list -->` flag line survives and insertion lands correctly after normalization), and configure `extra_changelog_sections` to keep today's section titles.

Its honest risk is posture, not function: pre-1.0, primarily one maintainer, schema still evolving (`--upgrade` migrates deprecated syntax). Mitigations: the CLI is pinned like any other CI tool, the config is small and declarative, and per D13 the switching cost to the fallback is a config rewrite, not a redesign.

**Fallback: [release-please](https://github.com/googleapis/release-please)** (the action, dispatch-only). Mainstream and verified workable: all five manifest stamps — including the oci `:vX.Y.Z` substring, the `.mcp.json` `==` pin, and `uv.lock`'s self-version — are expressible as built-in `extra-files` updaters (verified byte-level), and the rc→stable bug (googleapis/release-please#2515) is routed around via per-branch configs plus a one-commit `Release-As: X.Y.Z` finalize. It sits second on ceremony and shape: the two-phase dispatch, the finalize footer commit, the force-push-regenerated PR branch nothing else may touch, the finalize-on-branch config wrinkle, an action that lags the library, and an upstream responsive to Google's needs rather than the community's. All workable — which is exactly what a fallback must be.

**Retired: PSR.** A release-PR mode is architecturally absent and was declined upstream (python-semantic-release#355); its model — compute, commit, tag, push in one unreviewable shot — is precisely the shape being removed. Building the flow by hand (v1's recommendation) is likewise rejected per the adopt-don't-build preference.

## 7. What the redesign deletes

- PSR itself: config block, action, the deprecated `angular` parser concern (template#370).
- `scripts/bump_manifests.py` in its current form — replaced by native `versioned_files` for `pyproject.toml`/`uv.lock` plus a slimmed per-surface stamp step, running on a normal runner with real tooling available.
- The merge-back machinery: `scripts/merge_back.sh`, its tests, the retry loop — the deadlock it guarded against no longer exists.
- The `finalize` generated-config override and the `force` input (replaced by the plain promotion run and the explicit override).
- Admin-bypass direct pushes of release commits and merge-backs to `main`.
- Prediction-asserting rc tags, and the class of defects behind template#154 and #235.
- The post-release-only limitation of the notes pipeline: notes become draftable at rc time. The notes pipeline itself — agent PR against `docs/releases/`, merge-is-publish body/docs refresh — survives in simplified form (D6).

## 8. What survives unchanged

- The release *model* prose: trunk-first, value-triggered cadence, branch-as-exception, quiescence signals (advisory), "merging is not releasing" — only the mechanics under it change. A feature merge feeds `edge`; a release-PR merge releases.
- Conventional commits and the PR-title gate, at full weight (D8).
- Ordering-aware rolling pointers (Docker `latest`/`vX`/`vX.Y`, GH latest-release, docs `latest`, marketplace, registry) — a backport never repoints them backwards.
- The notes evidence contract, the one-page-per-minor format, `RELEASE-SUMMARY` markers, and the machine/narrative boundary between `CHANGELOG.md` and `docs/releases/`.
- The release-contract tests, rewritten to assert the same invariants against the new prepare step: rc leaves PyPI-pins untouched; stable satisfies per-surface resolvability on every published field; promotion diff is stamps-only; the changelog insertion flag survives.
- Per-branch release concurrency; one tag, one producer; immutable tags.

## 9. Open questions (to settle at design review, not to grow)

1. **Live end-to-end spike** on a scratch GitHub repository with the real workflows: dispatch → `knope prepare-release` → PR, merge-triggered `Release`, the branch-derived rc flag, the same-source promotion guard, and the rulesets interaction (which token the tag push runs with so `release: published` workflows still fire). The CLI behaviors are verified locally; the workflow wiring is not yet. First implementation step, and the go/no-go for the fallback.
2. **Per-surface stamping choices** (D12): which manifests keep committed stable-only stamps versus moving to publish-time rendering like the mcpb channel. Constraint to respect: the plugin channel serves committed files at a ref, so it likely keeps committed stamps.
3. **First release under the new system** and the disposition of `v4.0.0-rc.1` / `release/4.0` — including whether to yank the rc tag to start the 4.0.0 series cleanly under knope's numbering. A migration question, parked until the vision is agreed.

Implementation is template-first (`fastmcp-server-template` owns `release.yml`, the release-contract tests, and this configuration; an mvm-first build would be clobbered by the next `copier update`), adopted here through a released template version. Sequencing that migration is the next document, not this one.
