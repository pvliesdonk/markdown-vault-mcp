# Release system migration: from PSR to the release-PR flow

- **Status:** proposal v2, 2026-08-18. v1 sequenced the move to the model in [`release-vision.md`](release-vision.md) (merged via #1088); v2 is amended after a three-source critical review (two adversarial passes against the real machinery plus the PR review) — the substantive changes are M4's honest knope-conventions delta, the new M6 guard-scope decision resolving the notes/guard collision, the P1 inertness interlock, the contract-test respecification, and the emergency-release path (§3).
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
| Post-release-only notes pipeline (`release-notes.yml` + `release-notes-publish.yml`, interim-body marker) | notes PR draftable at prepare time; conditional docs pointer; simplified publish half survives with a release-time page check | rework triggers + skill range; keep merge-is-publish body/docs refresh (P1.5, P2.3) |
| `minor_tags`/`patch_tags` (`feat`; `fix`, `perf`) + per-type changelog sections | knope: `feat`/`fix`/`!` only; three sections (Breaking/Features/Fixes) | the honest conventions delta is M4 — not a mechanical swap |
| `tests/test_release_contract.py` asserting PSR config/changelog flag/assets | same *intent* asserted against knope config + stamp step + guard, respecified for the release-PR intermediate state | rewrite per P1.6 (dual-tracked until P2) |
| `edge` channel, publish fan-out job graph, ordering-aware rolling pointers, mcpb composite, rulesets | unchanged | none (re-wired triggers only) |

Everything in the last row is deliberately untouched: the migration replaces the *version/tag/changelog core*, not the publishing pipeline around it. Beyond the table, PSR is *named* in prose and comments across both repos (CLAUDE.md.jinja sections outside the release chapters, `scripts/check_pr_title.py`'s docstring and revert caveat, the seeded `CHANGELOG.md` intro, comments in `docs.yml`/`bootstrap.yml`/`ci.yml`/`copier.yml`) — P2.4 carries the sweep checklist so none of it survives as false documentation.

## 1. Decisions this document makes (resolving vision §9.2–9.4 and review findings)

**M1 — Token wiring (vision §9.2, both halves): `RELEASE_TOKEN` on the release step *and* on PR creation.** knope's `Release` step (tag + GitHub release) runs with `RELEASE_TOKEN`, so the verified `GITHUB_TOKEN` trigger-suppression never bites: `docs.yml`'s `release: published` deploy and the notes flow (the only event consumers — enumerated across both repos) keep firing unchanged. `CreatePullRequest` also uses `RELEASE_TOKEN`: a `GITHUB_TOKEN`-authored PR triggers no workflows, so `CI Success` — a required check on `main` — would never report and the release PR would be unmergeable forever (the same reason `copier-update.yml` already uses `RELEASE_TOKEN`). The spike's "repo setting" alternative is therefore moot for this fleet and dropped. The token's scope still shrinks exactly as vision D4 promises: from "push release commits and merge-backs to protected branches" to "create one tag + release, open the release/notes/port PRs".

**M2 — Per-surface stamping (vision §9.3): keep committed stable-only stamps, via the stamp script.** `server.json` and the two plugin manifests keep committed pins at the last stable, stamped only on stable prepares — the plugin channel serves committed files at a ref and `mcp-publisher` reads `server.json` at the tag, so both need the values in the release commit. `pyproject.toml` and `uv.lock` (self-version line only) are knope `versioned_files`. The mcpb channel stays template-rendered (`${VERSION}`), unchanged. This is exactly the spike-verified configuration; no surface moves to publish-time rendering in this migration.

**M3 — First release (vision §9.4): 4.0.0 continues from trunk — no branch, no yank.** Verified on the real repo (and independently re-verified in review via the API): `v4.0.0-rc.1` is **reachable from `main`** (the final PSR merge-back carried it), and knope continues a series from the highest reachable rc tag of the target version; the ≥4 breaking commits since `v3.1.0` fix the target at 4.0.0 and nothing since rc.1 can push past it. So the series proceeds as `prepare (rc)` on `main` → `4.0.0-rc.2` → promote → `4.0.0`, all from trunk. This is a deliberate doctrine extension M3 owns: an rc may be cut from quiescent trunk when it continues a reachable series — the surviving CLAUDE.md channel-table prose ("cut only from `release/X.Y`") is amended accordingly in P2.4. `release/4.0` is not needed for the cut and is deleted after 4.0.0 ships; the rc.1 tag stays (immutable history; PEP 440 ordering is correct).

**M4 — Conventions: the gate stays, and the knope delta is stated, not papered over.** `scripts/check_pr_title.py`, the PR-title CI job, and the accepted-type list stay (history hygiene, and the release PR itself must pass the gate). But knope is not a drop-in for PSR's tag lists — verified in its docs/source and the hands-on evaluation:

- knope counts **only `feat`, `fix`, and `!`/`BREAKING CHANGE`**. All other types are silently ignored for both version and changelog — the same silent-drop failure class the PR-title gate was built around, so the gate's rationale carries over verbatim; the rewritten contract/convention tests assert this boundary explicitly.
- **`perf:` no longer cuts a patch by itself.** Accepted: a perf-only range that must ship is released with the explicit version override (or the change is titled `fix:` when it genuinely fixes a performance defect). CLAUDE.md's "fix and perf cut a patch" prose is corrected in P2.4.
- **The changelog shrinks to three sections** (Breaking Changes / Features / Bug Fixes — knope's change-type sections, renamed via `extra_changelog_sections`; per-commit-type sections for chore/docs/refactor/etc. are not a capability knope has for commits). This is vision D7 applied honestly: `CHANGELOG.md` is a plain audit trail; richness lives in `docs/releases/`.
- **`revert:` no longer reaches the changelog either** (PSR's one advantage here dies with it). The revert caveat in `check_pr_title.py` and CLAUDE.md is rewritten: both revert forms are changelog-invisible; the notes page narrates reverts, as it already does.

The three-way lockstep test (`test_commit_conventions.py`) drops its PSR leg and asserts the new reality (gate list ↔ CLAUDE.md prose ↔ the knope-counted subset called out as such). CHANGELOG heading style migrates once: `## vX.Y.Z` → `## X.Y.Z` (the `<!-- version list -->` flag line is kept — insertion verified to land correctly after normalization).

**M5 — Issue hygiene precedes code.** Epic #1082's 17 template children (fastmcp-server-template#388–#404) prescribe the superseded transaction model; they are closed as superseded by the merged vision, replaced by *one* new template epic ("adopt the release-PR flow", children per phase below, with a **release milestone** as the primary quiescence signal and the `ships-atomically` label as fallback, per the cut-criterion convention — the swap phase is atomic). The 18th child, mvm#1086, is re-scoped to the Phase-3 adoption. #1082 itself closes when this migration document merges, with a closing comment pointing at the vision and the new epic; **no edit to the merged vision doc** — its §9 already marks the questions as parked, and this document's §1 is the durable resolution record. Resolved-by-migration issues get linked for closure by the implementing PRs: template#370 (deprecated `angular` parser — moot with PSR gone), template#371 (rc-time notes — vision D6 + P1.5), template#387 (tag-less CI checkouts — absorbed into P1.6 *with* the checkout fix). mvm#1053 is verified against the new stamp semantics and closed when adoption lands.

**M6 — The promotion guard's allowed-path set includes the notes page.** Vision D6 (notes merge in parallel, rc phase included) and D10 (stamps-only promotion diff) collide on a trunk cut: the notes PR for the very release being promoted lands on `main` between rc and stable, and a stamps-only guard would refuse the promotion — burning an rc on a docs page. Resolution: the guard's allowed set is *release stamps + `docs/releases/**` (and its index)* — notes are release metadata, exactly like the changelog section already in the set. **Everything else remains unconditional**: any other commit (a Renovate bump, an ordinary merge) landing on trunk between the last rc and the promotion forces a new rc — that is the price of trunk cuts, stated plainly; hold merges during a short soak, or use a stabilisation branch when trunk cannot pause.

## 2. Phases

Dependency chain: P0 → P1 → P2 → P3 → P4. P1 is additive and interlocked-inert; P2 is the atomic swap inside the template; P3 is one copier update downstream; P4 is a release, not a code change.

### Phase 0 — Decisions and issue hygiene (no code)

- Land this document (mvm PR, `Refs #1082`); apply M5: close template#388–#404, open the milestone-carrying template epic with per-phase children, re-scope #1086, close #1082.

**Exit:** issue tree matches the plan; nothing else changed.

### Phase 1 — Template: knope core, additive and interlocked

All in `fastmcp-server-template`. "Inert" is enforced, not assumed: the rendered workflows are dispatchable the moment they exist, so —

0. **Interlock:** both new workflows — the dispatchable prepare *and* the merge-triggered tag-release — refuse while `[tool.semantic_release]` still exists in the rendered `pyproject.toml` (a one-line mechanical check, ahead of the guard/Release steps). This keeps both release paths from ever being live at once — a merged prep-named PR cannot invoke Release during the additive phase any more than a dispatch can — and doubles as the swap-completeness test: P2's deletion of the PSR block is what arms the new flow.
1. **`knope.toml.jinja`** — package config (`pyproject.toml` + `uv.lock` regex `versioned_files`, changelog, `extra_changelog_sections` renaming knope's three sections to today's titles), `prepare-release` workflow (PrepareRelease → stamp Command → commit → push → CreatePullRequest) and `tag-release` workflow (guard Command → Release), `[github]` block templated from copier answers. The `CreatePullRequest` title is pinned to `chore: prepare release $version` — the release PR must itself pass the PR-title gate. Base is runtime-patched in the workflow (knope 0.23.0's `base` is not templatable — spike-verified).
2. **`scripts/stamp_manifests`** (successor of `bump_manifests.py`) — stable-only stamping of `server.json` + plugin manifests, fail-loud and atomic (the #1083 spike's lesson: no warn-and-continue), `DOMAIN-MANIFESTS-HELPERS`/`DOMAIN-MANIFESTS` sentinels preserved. Runs on a normal runner — the stdlib-only constraint dies with PSR's container.
3. **`scripts/promotion_guard`** — the spike's guard with M6's allowed set (stamps + `docs/releases/**`): skip for prereleases and rc-less trunk releases; allowed-paths-only diff vs the highest reachable rc of the target; non-zero exit refuses **before** knope's Release step.
4. **Workflows**: dispatch-only prepare with `channel` input (rc default on `release/*`, stable selected explicitly — the spike's channel-derivation trap), always recreating the prep branch from base (never re-running on a stale branch — double-bump trap); the prep branch is **namespaced by its base** (e.g. `knope/prepare/<base>`, so `knope/prepare/main` and `knope/prepare/release-3.1` coexist) — a single shared prep head would let a backport dispatch force-push over an open trunk release PR and make concurrent release PRs indistinguishable to the tag workflow, breaking the per-branch release concurrency the vision preserves; the namespace also keeps prep branches **outside `release/**`** so that ruleset's non-fast-forward rule never blocks the recreate-and-force-push flow. Each base gets its own concurrency bucket (mirroring today's `release-${ref}` buckets). The merge-triggered tag-release is keyed on the prep-branch namespace, running with `RELEASE_TOKEN` (M1). Two operator rules documented with the workflows: **never use GitHub's "Update branch" on a release PR** — it merges base into the prep branch behind the tool's back and routes around the recreate rule; the only refresh is re-dispatching prepare. And note the ruleset's strict required checks already *hard-block* merging a stale release PR here — the vision's "advisory" staleness check is the early warning, the ruleset is the wall.
5. **Notes-flow rework**: the prepare-time entry is a parallel resolve scheme, not a parameter — today's workflow hard-fails without an existing tag and keys its branch and markers on the tag name. New resolution: research range = *newest stable strictly below the target version* (the existing three-workflow idiom; for a first-of-series page like 4.0 that is the last 3.x stable) … the release PR's source commit; branch keyed on the target version; refreshed alongside re-dispatch. `writing-release-notes` SKILL.md updated accordingly. The publish half survives with two amendments: its upgrade-once sentinel becomes "body lacks the notes deep-link line" (the interim-marker grep dies with the marker), and the **stable release step gains the page check** — on publish, if the series page exists on `main`, add the deep link and (for branch-cut stables, whose tag lacks `main`'s page) perform the overlay redeploy that today's post-merge path handles; without this, rc-time-merged notes would hit the publish half's "no stable yet" skip and nothing would ever re-fire it.
6. **Contract tests, dual-tracked and respecified.** The new tests land *alongside* today's PSR tests (which keep guarding the still-live path until P2 deletes them with the machinery). Specification, corrected for the release-PR intermediate state: committed pins must equal the last stable **or** the version this diff prepares (a stable release PR is exactly the state the old tag-coupled asserts would reject — and under D1 it must pass CI); tags-absent behavior fails loudly *only when the repo demonstrably has releases* (changelog carries version sections) *and* tags were not fetched — a fresh render with no history keeps its skip, or `template-ci`'s smoke gate breaks; and `ci.yml`'s checkout gains `fetch-tags: true` **in the same PR** (the actual fix for template#387 — the failure mode is tag-less checkouts of tagged repos, not tag-less repos). The suite keeps a successor to today's invocation-coupling assert: the prepare workflow/knope.toml demonstrably *invokes* the stamp script, so a stamp declared in one half only cannot ship a stale file.
7. `template-ci` renders and gates all of it; the smoke render proves knope.toml + workflows are syntactically live even though the interlock keeps them refusing.

**Exit:** template main carries the complete new machinery, refusing by interlock; PSR path untouched, still guarded by its own tests; a template pre-release/tag exercises `copier copy` cleanly.

### Phase 2 — Template: the swap (atomic within the template)

One PR (or one tightly-stacked series merged together), because the pieces are useless apart. This is a large-blast-radius diff by design — budget review effort accordingly; the P1 interlock means the swap is also the arming action.

1. Rewrite `release.yml.jinja`: delete the PSR step, `force`/`finalize` inputs, the finalize override, and the `merge-back` job; the publish fan-out jobs (ordering computation, body step, PyPI, Docker, packages, mcpb, marketplace, registry) survive re-keyed to the tag-release trigger and knope's outputs — prerelease-ness from the `-rc.` tag substring, exactly as gates derive today from PSR outputs.
2. Delete `[tool.semantic_release]` from `pyproject.toml.jinja` (arming the P1 interlock), `scripts/merge_back.sh`, `tests/test_merge_back.py`, and the old PSR contract tests (ending P1.6's dual-track); add the branch-release bookkeeping port-PR step (changelog + notes delta to `main`, ordinary PR, advisory staleness nag).
3. Release body contract: machine changelog + conditional docs pointer with the P1.5 page check; retire the interim-marker handshake.
4. Prose in lockstep — the sweep checklist: `CLAUDE.md.jinja` (Conventions incl. the M4 delta and revert caveat, Breaking Changes' PSR mention, Hard PR Gates' manifest-lockstep wording, Documentation Discipline's CHANGELOG bullet, Release model incl. the M3 trunk-rc amendment to the channel table, Release machinery/merge-back → the new flow, manifest extension points, plugin channel, repository protection); `scripts/check_pr_title.py` docstring + revert caveat; `CHANGELOG.md.jinja` seed intro; comment-level references in `docs.yml.jinja`, `bootstrap.yml.jinja`, `ci.yml.jinja`, `copier.yml`; `docs/deployment/release-process.md.jinja`; CONTRIBUTING pointers.
5. One-time downstream migration notes in the template changelog/update guide: CHANGELOG heading normalization command; **copy any `DOMAIN-MANIFESTS` sentinel content into `stamp_manifests` before updating** — copier deletes template-removed files outright, destroying downstream sentinel content with them (recoverable from git history if the update already ran); deleting local PSR expectations.
6. **Template release: major bump** via `template-release.yml` — the operator surface of every generated project changes (dispatch inputs, merge-back gone, bump script replaced, `perf` semantics). This is the template's own breaking-change test applied honestly.

**Exit:** a tagged template version whose generated projects release via the new flow; PSR exists nowhere in the render.

### Phase 3 — MVM adoption (one copier update + one hand pass)

1. `copier update` to the new template tag. Re-rendered: workflows, CLAUDE.md, contract tests, stamp/guard scripts. `bump_manifests.py` is deleted by the update (verified against copier's behavior: template-removed files are removed downstream unconditionally) — MVM's `DOMAIN-MANIFESTS` sentinels contain only comments (verified), so nothing is lost here; the P2.5 note exists for downstreams where that is not true.
2. Hand pass in the same PR: CHANGELOG heading normalization (`sed 's/^## v/## /'` on version headings, flag line untouched) and its PSR-naming intro line (the file is `_skip_if_exists`, so only the hand pass can touch it); verify `.mcp.json`/`server.json`/plugin pins sit at the last published stable (closes mvm#1053 or files the correction); remove any local PSR references outside template ownership.
3. Full gates + a `--dry-run` of `knope prepare-release` as the adoption smoke test (the interlock is gone with the PSR block, so the dry run exercises the real path).
4. Close #1086.

**Exit:** MVM main releases only via the new flow; nothing has been released with it yet.

### Phase 4 — Release 4.0.0 (the proof, not the goal)

Per M3, all from trunk:

1. Advisory quiescence check honored for real: the 4.0 milestone / `ships-atomically` epics must be quiet (this migration's epic closes at P3).
2. Dispatch prepare (`channel: rc`) on `main` → release PR for **4.0.0-rc.2** (knope continues past the reachable PSR-era rc.1); notes PR for the 4.0 page drafted in parallel with the P1.5 range rule (last 3.x stable … the release PR's source). Review both; merge the release PR → rc.2 tags and publishes (Docker + GH prerelease + mcpb only; manifests untouched). **Merge the notes PR during the rc window** — M6 admits it into the promotion diff, so it costs nothing.
3. Soak briefly, holding other merges to `main` — any non-allowed commit landing now forces rc.3 (M6, unconditional). A fix that must go in *is* rc.3; the target cannot be overtaken either way.
4. Dispatch prepare (`channel: stable`) → promotion PR for **4.0.0**; guard asserts the M6 allowed set against the last rc (pre-tag); merge → tag, full stable fan-out, docs `4.0`, marketplace/registry, release body = changelog + notes deep link (page already on `main`). The two security fixes (#1029, #934) finally ship.
5. Cleanup: delete `release/4.0` (contents merged long ago); close #1055; the 4.0 notes page finalizes through its own PR if follow-ups remain.

**Exit:** the first stable under the new system is out; the old rc series' pathology (a target that can be overtaken) is demonstrably gone.

## 3. Mid-migration operations

**Emergency release before P3 (PSR still live):** never dispatch a stable on `main` — trunk already carries the 4.0 breaking range, so a PSR stable *is* 4.0.0, consumed mid-flight and wrecking M3's plan. The correct move is the existing backport tool: `release/3.1` retroactively from `v3.1.0`, cherry-pick the fix, dispatch with `finalize` → `3.1.1`, merge-back runs as today.

**Emergency release after P3 (new flow live, 4.0.0 not yet out):** rc-from-HEAD then immediate promotion — two PR merges. A *direct* stable prepare on `main` is refused by the guard while the stale PSR-era rc.1 is the highest reachable rc, which is correct behavior, not an obstacle.

**Rollback:** phases 1–2 live behind template versioning — a downstream that never runs `copier update` keeps releasing with PSR indefinitely, so the template swap carries no fleet-wide risk. This safety rests on an explicit premise, verified: the weekly copier-update cron ships **disabled** in both the template and MVM — adoption is dispatch-driven; a consumer that enabled the cron locally will be offered the major-swap PR unprompted and must not arm auto-merge on it. After P3, MVM's rollback is `copier update` back to the previous template tag plus reverting the adoption PR — available until 4.0.0 ships, after which rolling back the machinery would not roll back the release and there is no reason to.

## 4. Explicitly out of scope

- Any change to `edge`, the mcpb composite, nfpm packaging, docs site structure, or the ordering-aware rolling-pointer logic (they survive verbatim; only their triggers' upstream changes).
- Moving any manifest to publish-time rendering (M2 keeps committed stamps; revisit only if a new install surface demands it).
- Ledgers, per-destination convergence verification, resumable publication — rejected in vision D14 and not resurrected here.
- Other template consumers' adoption schedules — the template major bump leaves them on the old flow until they update (dispatch-driven; see §3's cron premise).

## 5. Open questions (small, settled at implementation)

1. **knope pinning**: `knope-dev/action` + knope version pinned in the template and bumped by Renovate like any CI tool; confirm Renovate recognizes the action's `version:` input or add a regex manager.
2. **Naming**: whether the prepare/tag workflows fold into the existing `release.yml` filename (fewer files, keeps ruleset/docs references) or ship as two workflows. Cosmetic; decide in the P2 PR.
