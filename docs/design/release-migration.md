# Release system migration: from PSR to the release-PR flow

- **Status:** proposal, 2026-08-18. Sequences the move to the model pinned in [`release-vision.md`](release-vision.md) (merged via #1088). This document decides *order and routing*; it re-litigates no design decision — where it resolves a vision open question (§9.2–9.4 there), it says so explicitly.
- **Relates to:** epic #1082 (this migration is its resolution), #1055 (cut 4.0.0), #1086 (downstream adoption), fastmcp-server-template#370/#371/#387 (resolved or absorbed below).
- **Ground rules carried over:** template-first (the release machinery is copier-owned; an mvm-first build is clobbered by the next `copier update`); the old machinery stays fully functional until the swap phase, so there is never a moment with no release path; each phase lands green and shippable on its own.

## 0. What is being replaced, in one table

| Today (PSR) | Target (vision) | Migration action |
|---|---|---|
| PSR computes version at publish, commits, tags, pushes with admin bypass | knope computes version at *prepare* into a reviewed release PR; tag after merge | replace `release.yml`'s PSR step with prepare/tag-release workflows |
| `[tool.semantic_release]` config block | `knope.toml` | new file; PSR block deleted at swap |
| `scripts/bump_manifests.py` (PSR build_command, stdlib-only) | knope `versioned_files` (pyproject, uv.lock) + slim stamp script as Command step (server.json, plugin manifests) | rewrite as `scripts/stamp_manifests.*`, keep `DOMAIN-MANIFESTS` sentinels |
| `finalize` config-override hack + `force` input | promotion = plain (non-rc) prepare run; `--override-version` for the exceptional case | delete |
| Mandatory merge-back (`scripts/merge_back.sh` + job + tests) | ordinary bookkeeping port PR for branch releases | delete script/job/tests; add port-PR step |
| Post-release-only notes pipeline (`release-notes.yml` + `release-notes-publish.yml`, interim-body marker) | notes PR draftable at prepare time (range: last stable of series … release-PR source); conditional docs pointer; simplified publish half survives | rework triggers + skill range; keep merge-is-publish body/docs refresh |
| `tests/test_release_contract.py` asserting PSR config/changelog flag/assets | same invariants asserted against knope config + stamp step + guard | rewrite |
| `edge` channel, publish fan-out job graph, ordering-aware rolling pointers, mcpb composite, rulesets | unchanged | none (re-wired triggers only) |

Everything in the last row is deliberately untouched: the migration replaces the *version/tag/changelog core*, not the publishing pipeline around it.

## 1. Decisions this document makes (resolving vision §9.2–9.4)

**M1 — Fan-out wiring (vision §9.2): keep the App/PAT token on the release step.** knope's `Release` step (tag + GitHub release) runs with `RELEASE_TOKEN`, so the verified `GITHUB_TOKEN` trigger-suppression never bites: `docs.yml`'s `release: published` deploy and any other event consumers keep firing unchanged. The token's scope shrinks exactly as vision D4 promises — from "push release commits and merge-backs to protected branches" to "create one tag + release" (plus opening the bookkeeping/notes PRs so CI runs on them, as today). The alternative (chaining all publishing off the tag-creating run) was rejected as the larger diff: the publish jobs already live in one workflow and only `docs.yml` and the notes flow listen for events.

**M2 — Per-surface stamping (vision §9.3): keep committed stable-only stamps, via the stamp script.** `server.json` and the two plugin manifests keep committed pins at the last stable, stamped only on stable prepares — the plugin channel serves committed files at a ref and `mcp-publisher` reads `server.json` at the tag, so both need the values in the release commit. `pyproject.toml` and `uv.lock` (self-version line only) are knope `versioned_files`. The mcpb channel stays template-rendered (`${VERSION}`), unchanged. This is exactly the spike-verified configuration; no surface moves to publish-time rendering in this migration.

**M3 — First release (vision §9.4): 4.0.0 continues from trunk — no branch, no yank.** Verified on the real repo: `v4.0.0-rc.1` is **reachable from `main`** (the final PSR merge-back carried it), and knope continues a series from the highest reachable rc tag of the target version. So the 4.0.0 series proceeds as `prepare (rc)` on `main` → `4.0.0-rc.2` → promote → `4.0.0`, all from trunk. `release/4.0` is not needed for the cut; it is deleted after 4.0.0 ships. The rc.1 tag stays (immutable history; PEP 440 ordering is correct).

**M4 — Conventions kept at full weight, one contract retired.** `scripts/check_pr_title.py`, the PR-title CI job, and the type list stay: they feed the changelog and history hygiene (vision D8). The three-way lockstep test (`test_commit_conventions.py`) drops its PSR leg (`allowed_tags` in pyproject disappears) and gains a knope leg where applicable (`extra_changelog_sections`). CHANGELOG heading style migrates once: `## vX.Y.Z` → `## X.Y.Z` (knope's format; the `<!-- version list -->` flag line is kept — insertion verified to land correctly after normalization).

**M5 — Issue hygiene precedes code.** Epic #1082's 18 template children (fastmcp-server-template#388–#404) prescribe the superseded transaction model; they are closed as superseded by the merged vision, replaced by *one* new template epic ("adopt the release-PR flow", children per phase below, `ships-atomically` label — the swap phase is atomic). #1086 is re-scoped to the Phase-3 adoption. #1082 itself closes when this migration document merges, with a closing comment pointing at the vision and the new epic. Resolved-by-migration issues get linked for closure by the implementing PRs: template#370 (deprecated `angular` parser — moot with PSR gone), template#371 (rc-time notes — vision D6 + notes-range work), template#387 (tag-less CI checkout skipping lockstep tests — the rewritten contract tests must fetch tags or fail loudly instead of skipping, absorbed into Phase 1). mvm#1053 is verified against the new stamp semantics and closed when adoption lands.

## 2. Phases

Dependency chain: P0 → P1 → P2 → P3 → P4. P1 is additive and inert; P2 is the atomic swap inside the template; P3 is one copier update downstream; P4 is a release, not a code change.

### Phase 0 — Decisions and issue hygiene (no code)

- Land this document (mvm PR, `Refs #1082`); apply M5: close template#388–#404 + re-scope #1086; open the template epic with per-phase children; close #1082.
- Record M1–M4 as accepted in the vision doc's §9 (one small follow-up edit turning the open questions into pointers here).

**Exit:** issue tree matches the plan; nothing else changed.

### Phase 1 — Template: knope core, additive and inert

All in `fastmcp-server-template`, rendered but *not yet triggered by anything real* (the PSR path remains the live one):

1. **`knope.toml.jinja`** — package config (`pyproject.toml` + `uv.lock` regex `versioned_files`, changelog, `extra_changelog_sections` matching today's section titles), `prepare-release` workflow (PrepareRelease → stamp Command → commit → push → CreatePullRequest) and `tag-release` workflow (guard Command → Release), `[github]` block templated from copier answers. Base for `CreatePullRequest` is runtime-patched in the workflow (knope 0.23.0's `base` is not templatable — spike-verified).
2. **`scripts/stamp_manifests`** (successor of `bump_manifests.py`, same filename semantics decided at implementation) — stable-only stamping of `server.json` + plugin manifests, fail-loud and atomic (the #1083 spike's lesson: no warn-and-continue), `DOMAIN-MANIFESTS-HELPERS`/`DOMAIN-MANIFESTS` sentinels preserved so downstream domain entries survive `copier update`. Runs on a normal runner — the stdlib-only constraint dies with PSR's container.
3. **`scripts/promotion_guard`** — the spike's guard, generalized: skip for prereleases and rc-less trunk releases; stamps-only diff vs the highest reachable rc of the target; non-zero exit refuses **before** knope's Release step.
4. **Workflows** (`prepare.yml`-shaped, names decided at implementation): dispatch-only prepare with `channel` input (rc default on `release/*`, stable selected explicitly — the spike's channel-derivation trap), always recreating the prep branch from base (never re-running on a stale branch — double-bump trap); merge-triggered tag-release keyed on the prep branch head, running with `RELEASE_TOKEN` (M1).
5. **Notes-range generalization**: the notes workflow gains a prepare-time entry point (range: last stable of series … release-PR source commit; refreshed on re-dispatch) alongside the existing tag-based one; `writing-release-notes` SKILL.md updated accordingly; the publish half (`release-notes-publish.yml`) survives simplified — conditional pointer, no interim-marker grep once the new body contract lands in P2.
6. **Contract tests rewritten** (`test_release_contract.py.jinja`): knope config sanity (versioned_files set, changelog file), stamp-script behaviors (rc leaves pins, stable bumps every published field, lockstep, sandboxed like today), guard presence + refusal semantics, and the promotion stamp-set enumeration; tests **fail loudly instead of skipping** when tags are absent (absorbs template#387).
7. `template-ci` renders and gates all of it; the smoke render proves knope.toml + workflows are syntactically live even though nothing dispatches them.

**Exit:** template main carries the complete new machinery, dormant; PSR path untouched and still the default; a template pre-release/tag exercises `copier copy` cleanly.

### Phase 2 — Template: the swap (atomic within the template)

One PR (or one tightly-stacked series merged together), because the pieces are useless apart:

1. Rewrite `release.yml.jinja`: delete the PSR step, `force`/`finalize` inputs, the finalize override, and the `merge-back` job; the publish fan-out jobs (ordering computation, body step, PyPI, Docker, packages, mcpb, marketplace, registry) survive re-keyed to the tag-release trigger and knope's outputs — prerelease-ness from the `-rc.` tag substring, exactly as gates derive today from PSR outputs.
2. Delete `[tool.semantic_release]` from `pyproject.toml.jinja`, `scripts/merge_back.sh`, `tests/test_merge_back.py`; add the branch-release bookkeeping port-PR step (changelog + notes delta to `main`, ordinary PR, advisory staleness nag).
3. Release body contract: machine changelog + conditional docs pointer (vision §4.3 as amended); retire the interim-marker handshake.
4. Prose in lockstep: `CLAUDE.md.jinja` (Conventions' PSR paragraphs, Release machinery, merge-back section → replaced by the new flow; the release *model* section survives nearly verbatim per vision §8), `docs/deployment/release-process.md.jinja`, repository-protection doc (token now tags only), CONTRIBUTING pointers.
5. One-time downstream migration notes in the template changelog/update guide: CHANGELOG heading normalization command, the repo setting *or* App-token choice for PR creation (spike-verified both ways), deleting local PSR expectations.
6. **Template release: major bump** via `template-release.yml` — the operator surface of every generated project changes (dispatch inputs, merge-back gone, bump script replaced). This is the template's own breaking-change test applied honestly.

**Exit:** a tagged template version whose generated projects release via the new flow; PSR exists nowhere in the render.

### Phase 3 — MVM adoption (one copier update + one hand pass)

1. `copier update` to the new template tag. Re-rendered: workflows, CLAUDE.md, contract tests, stamp/guard scripts (domain manifest entries migrate into the new sentinels — the plugin manifests are already template-fenced). `bump_manifests.py` disappears with the update (deliberately not `_skip_if_exists`).
2. Hand pass in the same PR: CHANGELOG heading normalization (`sed 's/^## v/## /'` on version headings, flag line untouched); verify `.mcp.json`/`server.json`/plugin pins sit at the last published stable (closes mvm#1053 or files the correction); remove any local PSR references outside template ownership.
3. Full gates + a `--dry-run` of `knope prepare-release` in CI or locally as the adoption smoke test.
4. Close #1086.

**Exit:** MVM main releases only via the new flow; nothing has been released with it yet.

### Phase 4 — Release 4.0.0 (the proof, not the goal)

Per M3, all from trunk:

1. Advisory quiescence check honored for real: the 4.0 milestone / `ships-atomically` epics must be quiet (this migration's epic closes at P3).
2. Dispatch prepare (`channel: rc`) on `main` → release PR for **4.0.0-rc.2** (knope continues past the reachable PSR-era rc.1); notes PR for the 4.0 page drafted in parallel with the generalized range. Review both; merge the release PR → rc.2 tags and publishes (Docker + GH prerelease + mcpb only; manifests untouched).
3. Soak briefly; any fix lands on `main` normally and, if needed, yields rc.3 — the target can no longer be overtaken.
4. Dispatch prepare (`channel: stable`) → promotion PR for **4.0.0**; guard asserts stamps-only against the last rc (pre-tag); merge → tag, full stable fan-out, docs `4.0`, marketplace/registry, release body = changelog + notes pointer. The two security fixes (#1029, #934) finally ship.
5. Cleanup: delete `release/4.0` (contents merged long ago); close #1055; the 4.0 notes page finalizes through its own PR if still open.

**Exit:** the first stable under the new system is out; the old rc series' pathology (a target that can be overtaken) is demonstrably gone.

## 3. Rollback

Phases 1–2 live behind template versioning: a downstream that never runs `copier update` keeps releasing with PSR indefinitely, so the template swap carries no fleet-wide risk. After Phase 3, MVM's rollback is `copier update` back to the previous template tag plus reverting the adoption PR — available until 4.0.0 ships, after which rolling back the machinery would not roll back the release and there is no reason to.

## 4. Explicitly out of scope

- Any change to `edge`, the mcpb composite, nfpm packaging, docs site structure, or the ordering-aware rolling-pointer logic (they survive verbatim; only their triggers' upstream changes).
- Moving any manifest to publish-time rendering (M2 keeps committed stamps; revisit only if a new install surface demands it).
- Ledgers, per-destination convergence verification, resumable publication — rejected in vision D14 and not resurrected here.
- Other template consumers' adoption schedules — the template major bump leaves them on the old flow until they update.

## 5. Open questions (small, settled at implementation)

1. **knope pinning**: `knope-dev/action` + knope version pinned in the template and bumped by Renovate like any CI tool; confirm Renovate recognizes the action's `version:` input or add a regex manager.
2. **PR-creation token**: `RELEASE_TOKEN` for `CreatePullRequest` too (no repo setting needed — likely, per M1's token already being present), or `GITHUB_TOKEN` + the "allow Actions to create PRs" setting (spike-verified working). Decide once, template-wide.
3. **Naming**: whether the prepare/tag workflows fold into the existing `release.yml` filename (fewer files, keeps ruleset/docs references) or ship as two workflows. Cosmetic; decide in the P2 PR.
