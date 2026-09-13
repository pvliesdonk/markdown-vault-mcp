---
name: applying-template-updates
description: >-
  Use when asked to work through, apply, resolve or review a template update
  pull request (the weekly `copier/update` branch opened by the copier-update
  workflow), or after running `copier update --trust` by hand: applies the upgrade
  notes, resolves conflict markers by the resolution policy, checks the
  seeded-once files the diff cannot show, and runs the gate.
---

<!-- ===== TEMPLATE-OWNED — re-rendered on template updates. ===== -->

# Applying template updates

`docs/deployment/template-updates.md` explains the update; this skill is the
procedure. Work on the `copier/update` branch the workflow opened (or the
branch where `copier update --trust` was run). Stop and ask, rather than
guess, at three points: a conflict hunk inside a sentinel block, an upgrade
note you cannot perform (a secret, a repository setting), and a seeded-file
change you are not certain applies to this project. Report at the end: what
you applied, what you skipped and why, and what is left for a human.

## 1. Read the pull request body

Extract from it: the previous and target template refs, the compare link,
the `UPGRADING.md` sections for the jump, and the list of files with
conflict markers. If the body has no upgrade notes, fetch `UPGRADING.md` at
the target ref from the template repository — it is an index whose released
`## vX.Y` sections point at per-minor `upgrading/vX.Y.md` files — and fetch
every per-minor file from the previous ref's minor through the target's,
reading each one whole. Never grep or tail your way through them: each file
is complete for its minor, and the index enumerates which files a jump
needs.

## 2. Apply every upgrade note

Each section is an instruction to a person. Perform it on the branch:
renames, rewritten seeded tests, `.gitignore` lines, `uv lock`. A note you
cannot perform (a secret to add, a repository setting to change) is a stop
point: list it for the human. Do not skip a note because the project looks
unaffected; check, then say so in the report.

## 3. Resolve conflict markers

Find them with `git grep -n '^<<<<<<< before updating$'`. A hunk has four
marker lines: `<<<<<<< before updating` (local side), `||||||| last update`
(common base), `=======`, `>>>>>>> after updating` (template side).
Resolving means keeping one side and deleting all four markers and the base
block. For each hunk:

- outside every sentinel block, keep the template side and move any local
  content it displaces into the nearest sentinel block, or note it as a change
  to propose to the template;
- inside a `DOMAIN-*` / `CONFIG-*` / `PROJECT-*` / `DOCKERFILE-*` sentinel
  block, stop and ask: the local side is the project's. Several of these wrap
  template-shipped content that a project extends rather than replaces
  (`PROJECT-EXTRAS`, `DOCKERFILE-APT-DEPS`, `DOCKERFILE-UV-EXTRAS`), so a
  template change inside one is legitimate — keep the project's additions and
  take the template's changes around them;
- inside a `GENERATED-*` region, resolve nothing: take either side and let
  `scripts/gen_config_surface.py` rewrite the region at the end of the update
  (it runs as an after-stage migration), then confirm with
  `python scripts/gen_config_surface.py --check`.

Finish with `git grep -nE '^(<<<<<<<|\|\|\|\|\|\|\||=======|>>>>>>>)'` to prove
none remain.

## 4. Check the seeded-once files

Read `.copier-seeded-changes.md` at the repository root: the update wrote
it, and it holds a diff of every `_skip_if_exists` path between the previous
and target template refs (generated artefacts such as `.env.example` are
already excluded), or states that nothing changed. If it says the report
could not be computed, fall back to the compare link between the two refs
and the template's `UPGRADING.md` "Before every upgrade" recipe. A changed
seeded file is a change to apply by hand to this project's copy, preserving
local content; when you are not certain the change applies here, stop and
ask instead of applying or dismissing it. Describe each one in your report
either way.

## 5. Refresh and gate

`uv lock` when a dependency floor changed, then the update gate from
`docs/deployment/template-updates.md` step 5, in full: `git diff --check`,
the conflict-marker grep, `uv lock --check`, `uv sync --all-extras
--all-groups --locked`, `uv run python scripts/gen_config_surface.py
--check`, ruff check and format, mypy, pytest, `uv run mkdocs build
--strict`, `uv run pre-commit run --all-files`, and `scripts/vendor_spa.py
--check` on an MCP Apps project. Fix what the update broke; do not weaken a
test to pass.

## 6. Commit and report

Commit on the branch with a message naming the template refs. The report
lists: notes applied, conflicts resolved (file, side kept), seeded files
changed upstream and what was done, gate results, and open questions.
