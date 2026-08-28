---
name: releasing
description: >-
  Use before any release work: the trunk release model, the unstable edge channel, the prepare/release/promotion machinery, release-notes pages, the pre-release artifact smoke test, and, when the plugin channel is enabled, the Claude Code plugin channel.
---

<!-- ===== TEMPLATE-OWNED — re-rendered on template updates. ===== -->

# Releasing


## Claude Code plugin channel

`.claude-plugin/plugin/` ships this server as a Claude Code plugin: `.claude-plugin/plugin.json` (identity + version) and an exec-form `.mcp.json` that launches the released PyPI package with `uvx --from <pkg>==<version>`. Both manifests are version-coupled to the *stable* release stream — `scripts/stamp_manifests.py` stamps and stages them inside each stable release PR, so the marketplace entry published by the release workflow always installs the version it points at. That entry is written into the catalog's `.claude-plugin/marketplace.json`, the only path Claude Code loads a marketplace from — a manifest anywhere else is not a marketplace, and the publish job fails rather than bumping one. The marketplace entry is a rolling pointer, so on rc prepares the script leaves both files at the last published stable rather than pointing everyone browsing the catalog at a candidate (`tests/test_release_flow_contract.py` gates the invocation pairing and this skip). The `env` block in `.mcp.json`, the plugin README, and any `skills/` directories are project-owned content: the files are seeded once and never re-rendered by template updates. Values in `env` may reference plugin `userConfig` entries as `${user_config.<id>}` declared in `plugin.json` — exec-form fields only; shell-form command strings reject the substitution.

To generate the plugin's configuration screen from the config surface instead of hand-editing it, declare a `files:` pair in `config-presentation.domain.yml`: the plugin.json path with `kind: claude-plugin-user-config` and a `fields:` map (same field specs as the mcpb install screen (see the `config-contract` skill)), plus the `.mcp.json` path with `kind: claude-plugin-env` and `fields_from:` naming the first entry. One fields map then drives both the `userConfig` object and the `${user_config.<id>}` env wiring, and `scripts/gen_config_surface.py --check` gates the pair against drift.

## Pre-release artifact smoke test

The `Pre-release check` workflow (Actions tab, `workflow_dispatch`) builds and validates the mcpb bundle from any branch at a caller-supplied version (default `0.0.0-dev`), uploads it as a workflow artifact for manual install testing, and can optionally attach it to a deletable `v<version>-rc` pre-release. It runs the exact same steps a real release runs — both call the shared `.github/actions/build-mcpb` composite — so a green pre-release check means the release path's bundle build is green too. A second composite, `.github/actions/setup-project-env`, holds the uv + interpreter-cache + `uv sync` sequence every job that runs `uv run` needs; `ci.yml`'s jobs call it, and a domain workflow added to this project should call it too rather than copying the steps — a copy freezes at today's uv pin, cache key, and sync flags while the composite moves on without it. It takes `python-version`, `sync` (set `"false"` for a job that only needs uv itself), and `sync-args`. Check out the repository first: a local composite resolves only after `actions/checkout`. Project-specific artifact assertions (extra bundles, plugin manifests) belong in `packaging/pre-release-checks.sh` — an executable script the workflow runs when present, with `VERSION` and `BUNDLE` exported; the file is project-owned, and template updates never touch it.

## Release model

`main` is trunk. Everything merges there, and **merging a feature is not releasing**: a merge feeds the rolling `edge` channel (next section) and nothing else. A release is a separate, deliberate event — dispatching the **Release Prepare** workflow, reviewing the release PR it opens, and merging that PR, which is what tags and publishes — and the sections below say when and from where.

**The default release path is trunk.** When a release is wanted and trunk is quiescent (no atomic epic mid-flight — the cut criterion below), dispatch Release Prepare on `main` and merge the release PR it opens. That cuts a stable: no branch, no ceremony. Most releases should look like this.

**The cut criterion.** An epic that ships atomically makes trunk unreleasable while it is open: releasing `main` ships HEAD, mid-story. Judge quiescence from the queryable signal the epic conventions record (see CONTRIBUTING.md's Epics section): preferably the **release milestone** — safe to cut means no open issues in the target release's milestone — with the **`ships-atomically` label** as the fallback when no milestone names a release yet. An open atomic epic with unclosed children means either release from a commit before the epic started (a `release/X.Y` branch cut from that commit) or wait. The Release Prepare workflow's advisory step surfaces both signals on every default-branch dispatch — a release-named milestone (`X.Y`) that still has open issues, and open `ships-atomically` epics (each with a count of its native sub-issues, so a cross-repo epic whose children live elsewhere stays visible); it warns and never blocks, because the cut may still be intentional.

**The `release/X.Y` branch is the exception tool**, for exactly two cases:

1. **Dirty trunk at cut time** — trunk carries unfinished work that must be excluded from the release. Cut the branch from the last quiescent commit behind HEAD, stabilise there (rcs), and finalize the stable.
2. **Patching a shipped release** — an already-released version needs a fix while `main` has moved on past it. The branch does not need to exist in advance: create it retroactively from the tag (`git branch release/X.Y vX.Y.Z`), land the fix, release.

Fixes flow trunk → branch only: land on `main` first, cherry-pick to the branch. There is no merge-back: after a branch release, the automated port-bookkeeping PR (see the release machinery section below) carries the changelog section to `main` as an ordinary reviewed PR — never features, and nothing deadlocks if it merges late.

**Branching cannot defer a structural refactor.** A refactor that changes existing behaviour makes every later cherry-pick across it conflict-prone, so a stabilisation branch buys no room for one. Land structural refactors early in a cycle, far from the next cut — not late, when a branch is about to be needed.

**Cadence: value-triggered, not time-boxed.** No fixed release train — a calendar cadence is wrong for a low-traffic project and adds nothing to a busy one. Release when unreleased user-visible work (features, fixes, and especially security patches) has accumulated, and never hold a release hostage to unfinished work: excluding that work is exactly what the branch tool is for. The failure mode to avoid is drift — weeks of unreleased commits held behind one open epic.

**The three channels and their promises:**

| Channel | Identity | Promise |
|---|---|---|
| `edge` | none — the commit is the identity | the newest merged code, rebuilt on every merge to `main`; rolling and disposable |
| rc | `vX.Y.Z-rc.N`, target computed and reviewed in the merged release PR | a stabilisation step toward exactly that version — normally cut from `release/X.Y`, or from quiescent trunk when continuing a series whose rc tags are reachable there. Its image also takes the rolling `rc` tag while the candidate is still ahead of the newest stable |
| stable | `vX.Y.Z` | a quiescent-trunk release (the default) or the promotion of an rc series (a plain, non-rc prepare over the same commits, guarded to a stamps-only diff) |

An rc is not "the latest build with a version number" — that job belongs to `edge`, which costs one build-and-push and leaves no tag, release, or version bump behind. Cut rcs only when genuinely stabilising a specific release.

## Unstable channel (rolling `edge` image)

The `Unstable channel` workflow (`.github/workflows/unstable.yml`) runs on every push to `main` (plus manual dispatch for seeding) and rebuilds two artifacts from that commit: the container image, pushed to `ghcr.io/pvliesdonk/markdown-vault-mcp:edge` — a fixed rolling tag whose contents each merge replaces — and an mcpb bundle built through the same shared `.github/actions/build-mcpb` composite the release path uses, uploaded as the `mcpb-bundle-edge` workflow artifact at the constant version `0.0.0-dev`. The channel is deliberately versionless: no git tag, no GitHub release, no version bump, no manifest change. "Run the latest merged code" costs one build-and-push and leaves nothing behind; the image's `org.opencontainers.image.revision` label carries the exact commit. The docs site's rolling `unstable` version deploys from the same trigger (`docs.yml`), so it tracks merged code the way `edge` does. Cutting a version-numbered pre-release (rc) remains the release-PR flow's job (see the release machinery section below); an rc image ships under its immutable `vX.Y.Z-rc.N` tag plus the rolling `rc` tag, which release.yml owns. The two rolling unstable tags answer different questions and have exactly one producer each — `edge` is the newest merged commit, `rc` the newest candidate — and neither workflow writes the other's tag. `rc` is ordering-gated the way `latest` is: it moves only while the candidate's version is still ahead of the newest stable, so a candidate for an already-released version, or one cut on an older `release/X.Y`, never pulls it backwards.

## Release machinery (prepare, release PRs, promotion)

**A release is a pull request.** Dispatching **Release Prepare** on the ref to release from (`main`, or a `release/X.Y` branch) has knope compute the version from the conventional commits since the last release in that ref's ancestry, stamp the version-coupled files (`pyproject.toml` and `CHANGELOG.md` natively; `uv.lock`'s self-version entry — every prepare — and the install-channel manifests — stable prepares only — via `scripts/stamp_manifests.py`), and open — or refresh — a release PR against that ref. The dispatch's `channel` input picks rc or stable (`auto` = rc on `release/*`, stable elsewhere), and the optional `override_version` input replaces the whole computation with an explicit version — the escape hatch for ranges knope counts nothing in, never the default. Only the default branch and `release/*` can be dispatched on or merged into; both workflows refuse anything else. Merging the release PR is the release decision: the **Release** workflow tags the merge commit, creates the GitHub release, and runs the publish fan-out, with every gate derived from the reviewed version string (`-rc.` marks a pre-release) and tag ordering — there is no dispatch flag to desync.

**Promotion is the plain run.** After an rc series, dispatching prepare with `channel: stable` on the same ref computes exactly `X.Y.Z` from the same commits — no finalize flag, no config override. The same-source guard (`scripts/promotion_guard.sh`) runs in **two layers**: at prepare time, inside `knope prepare-release` after the prep commit and before the push/PR steps, so a drifted promotion refuses without ever becoming a mergeable PR; and again **before** the tag is created, as the immutable-tag backstop for drift landing between prepare and merge. It refuses unless the diff since the last reachable rc touches only release stamps plus `docs/releases/**` (notes are release metadata); a refusal leaves no tag behind, and any other commit landing between rc and promotion forces a new rc.

Two operator rules are load-bearing: **never use GitHub's "Update branch" button on a release PR** — it merges the base into the prep branch behind the tool's back; the only refresh is re-dispatching Release Prepare, which recreates the prep branch from its base. And a stale release PR is already hard-blocked by the ruleset's strict required checks — re-dispatch instead of merging around staleness.

Rolling channels are ordering-aware: Docker `latest`/`vX`/`vX.Y` (and `rc` on the pre-release side), the GitHub latest-release pointer, the docs `latest` alias, the marketplace entry, and the registry entry only follow a release that is the newest in the relevant series, so a backport patch cut from an old `release/X.Y` never repoints them backwards — and the guarantee survives overlapping releases, because each mutable channel rechecks tag ordering inside its own serialized publish job.

**There is no merge-back.** What `main` still wants from a branch release is bookkeeping: after a *stable* branch release, the `port-bookkeeping` job in `release.yml` opens an ordinary PR carrying the release's changelog section and release-notes page to the default branch (rcs port nothing — knope's stable section is cumulative over the rc cycle) — reviewed CI-gated merge, no admin-bypass push, and nothing deadlocks if it merges late. Version stamps are deliberately not ported, so a later prepare on another branch can recompute an already-released version; Release Prepare catches exactly that — it refuses when the computed version already carries a repo-global tag, naming the remedy (`override_version`, or porting the version stamps first) — so the collision surfaces at prepare time with a clear message, never at tag time. When to cut a branch at all — and the default of releasing from a quiescent trunk without one — is the [release model](#release-model) above, not machinery.

<!-- TEMPLATE-TRACKING-START -->
## Release notes pages

`docs/releases/` is the canonical human-facing release narrative: one page per minor series, with undated patch-version sections. Before Release Prepare, invoke `.agents/skills/writing-release-notes/SKILL.md` and merge its ordinary pull request containing `docs/releases/next.md`. The skill researches through the GitHub API from linked issues and pull requests, never commit subjects. Release Prepare deterministically consumes staging into the target's canonical page after manifest stamping and before the release commit. The first rc consumes `next.md`; later rcs and stable promotion reuse the same stable `vX.Y.Z` entry. `main` accumulates canonical pages for all releases, including branch-cut bookkeeping ports, and the release body reads its summary and deep link from the tagged tree.

Rules for working with the notes:

- **The evidence contract governs the page.** Every causal claim must trace to a linked issue or PR; no evidence, no narrative. Reviewers compare `notes-range-end` with the release delta and follow each claim's evidence. Vale, including the `ai-tells` pack, and strict MkDocs gate prose and output.
- **There is no notes bypass.** Missing or ambiguous `next.md` state makes promotion refuse Release Prepare. If meaningful behavior is absent, invoke `refresh-known-target`, merge the normal notes pull request, and re-dispatch. Shipped-page backfills and redrafts use the same ordinary pull-request flow; **Release Notes Publish** remains deterministic.
<!-- TEMPLATE-TRACKING-END -->
