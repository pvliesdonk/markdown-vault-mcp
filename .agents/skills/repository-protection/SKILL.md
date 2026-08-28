---
name: repository-protection
description: >-
  Use when changing branch or tag rulesets, required checks, or the bootstrap workflow: how repository protection is applied and kept in sync.
---

<!-- ===== TEMPLATE-OWNED — re-rendered on template updates. ===== -->

# Repository Protection

## Repository protection (rulesets)

`.github/rulesets/*.json` are the source of truth for this repository's branch and tag rulesets — required PRs + the `CI Success` check on `main` and `release/*`, deletion/force-push protection for `v*` tags — and `bootstrap.yml` applies them (upsert by name) on pushes touching those files and on manual dispatch. Never adjust protection in the GitHub UI: the next bootstrap run resets it to the checked-in state. Change the JSON files instead. The release pipeline's `RELEASE_TOKEN` (a repository admin's PAT) bypasses these rules **by design** via the admin-role bypass entry — its remaining jobs are creating the `vX.Y.Z` tag + GitHub release after a release PR merges (the `v*` tag ruleset applies) and opening the release/notes/port PRs whose CI must run; direct release-commit and merge-back pushes to protected branches are gone with PSR. Posture and bypass model: `docs/deployment/repository-protection.md`.

A check that is not a job in `ci.yml` cannot join the `CI Success` aggregate — `needs:` only reaches jobs in the same workflow — so a domain workflow this project adds is advisory until its context is required outright. Declare it in the `extra_required_checks` answer instead, which renders into both branch rulesets alongside `CI Success`, rather than editing `ci.yml` or the ruleset files by hand. Every context listed there must report on *every* PR to a protected branch: a `paths:` filter or a false job-level `if:` reports nothing, and the merge then waits forever for a check that is not coming. Details and the workflow shape that avoids it: `docs/deployment/repository-protection.md`.
