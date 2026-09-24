---
name: applying-template-updates
description: >-
  Use when asked to work through, apply, resolve or review a template update
  pull request (the weekly `copier/update` branch opened by the copier-update
  workflow), after running `copier update --trust` by hand, or when a
  template update looks like it leaves no room for something this project
  needs.
---

<!-- ===== TEMPLATE-OWNED — re-rendered on template updates. ===== -->

# Applying template updates

**This is not a merge.** In most copier projects an update is mechanical:
take what the template changed, settle the conflicts, done. Here the
template is authoritative. In every file `copier update` re-renders, each
line outside a sentinel block belongs to the template, and the update's job
is to bring this project back to the template at the target ref. Conflict
markers show only where git happened to notice a collision. Drift the
template did not touch in this jump rides through without one, so an update
with no conflicts can still leave the project forked.

That drift was written by this project, not by the template: an earlier
commit put project content into lines the template owns. Your job is to
recognise it as the project's decay, not to rationalise it. An update is done
when every template-owned file conforms or has a Decay issue, not when the
markers are gone.

`docs/deployment/template-updates.md` explains the update for a human; this
skill is the procedure. Work on the `copier/update` branch the workflow
opened (or the branch where `copier update --trust` was run). Stop and ask,
rather than guess, at three points: a conflict hunk inside a sentinel block,
an upgrade note you cannot perform (a secret, a repository setting), and a
seeded-file change you are not certain applies to this project.

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

## 3. Read the drift report

`.copier-template-drift.md` at the repository root lists every
template-owned file that differed from the template outside its sentinel
blocks *before* this update, with the diff (`-` the template's line, `+` the
project's) and the commits that last touched those lines. The update wrote
it; the pull request body embeds it. If it is missing or says the
comparison could not be made, produce it yourself from the pre-update
commit (the base branch) and the previous template ref:

```bash
python scripts/check_template_conformance.py --rev origin/main --ref <previous-ref>
```

This list is the work. Conflict markers are the subset of it that the
template also touched. Read it before resolving anything: where a drifted
file also carries a conflict marker, the report tells you which side of the
hunk is the project's drift.

## 4. Resolve conflict markers

Find them with `git grep -n '^<<<<<<< before updating$'`. A hunk has four
marker lines: `<<<<<<< before updating` (local side), `||||||| last update`
(common base), `=======`, `>>>>>>> after updating` (template side).
Resolving means keeping one side and deleting all four markers and the base
block. For each hunk:

- outside every sentinel block, keep the template side, and handle the
  local content it displaces as in step 5;
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

## 5. Conform every drifted file

For each file in the drift report, whether or not it had a conflict marker,
restore the template's lines and put the project's content where the
template gives it room:

- into the sentinel block the rendered file declares for it (docs pages
  carry `DOMAIN-*` blocks for project prose; `config.py` carries
  `CONFIG-*` blocks for fields);
- into a module or page the template does not render, wired in through a
  sentinel block;
- into the project-owned file the template's line expects, when that is
  what is missing: a template import of a seeded module the project never
  created (`tools.py`, `prompts.py`, `resources.py`) is conformed by
  creating that module, even as a thin one delegating to the project's own;
- or nowhere, when the template's line already does the job.

When conforming a file needs design work beyond this update — taking the
template's line would break the project, and fixing that is a feature of its
own — keep the project's lines in that file, open a Decay issue in this
project with the file, the hunks from the report and the commits it names,
and record the issue number in your report. That is the only way a drifted
file stays drifted.

## 6. Check the seeded-once files

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

## 7. Refresh and gate

`uv lock` when a dependency floor changed, then the update gate from
`docs/deployment/template-updates.md`, in full: `git diff --check`, the
conflict-marker grep, `uv lock --check`, `uv sync --all-extras --all-groups
--locked`, `uv run python scripts/gen_config_surface.py --check`, ruff check
and format, mypy, pytest, `uv run mkdocs build --strict`, `uv run pre-commit
run --all-files`, and `scripts/vendor_spa.py --check` on an MCP Apps
project. Fix what the update broke; do not weaken a test to pass.

Then run `python scripts/check_template_conformance.py` against the working
tree. Every file it still lists must have a Decay issue from step 5.

## 8. Commit and report

Commit on the branch with a message naming the template refs. The report
lists: notes applied; conflicts resolved (file, side kept); every file from
the drift report with its outcome — conformed, or Decay issue number;
seeded files changed upstream and what was done; gate results; and open
questions for the stop points above.

## When the template looks wrong

It sometimes is. Tell the two cases apart by reproducing without this
project's content: render the target ref pristine with this project's
answers and run the failing check there.

```bash
copier copy --trust --defaults --data-file .copier-answers.yml \
  --vcs-ref <target-ref> <template-src> /tmp/pristine
```

- The fault shows in `/tmp/pristine`: it is a template defect. File it on
  the template repository (see `CONTRIBUTING.md` routing) with that
  reproduction.
- The fault shows only with this project's content in template-owned lines:
  it is this project's debt. Conform the file or open a Decay issue here.
- This project needs room the template does not give: that is a template
  feature request, stated as the need ("projects that do X need a seam at
  Y"), never as the content it would let this project keep. File it after
  the update conforms.

A template issue never resolves the update, and the update never waits for
one.

## Rationalisations to recognise

| What it sounds like | What it is |
|---|---|
| "The template leaves no room for this" | The drift report names the project commit that wrote it into template-owned lines. Move it into a sentinel block or file Decay. |
| "Taking the template side breaks the build" | The project diverged from the skeleton. Conform it, or keep the lines under a Decay issue. A template defect only if `/tmp/pristine` breaks too. |
| "One conflict, resolved; the update is clean" | Conflicts are a subset of the drift report. Clean means the conformance check lists nothing without a Decay issue. |
| "House style" / "we already had this" / "otherwise something would be missing" | Not reasons to keep drift. |
| "Propose a sentinel upstream so this can stay" | A seam is a need stated on its own merits, filed after conforming, never as the resolution. |
