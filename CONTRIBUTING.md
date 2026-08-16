# Contributing

Thanks for contributing. This guide covers how to file good issues and pull
requests, and where to send different kinds of fixes. It applies to both
human contributors and automated agents.

## Filing issues

Use the issue templates in `.github/ISSUE_TEMPLATE/`:

- **Bug report** — something isn't working as expected.
- **Feature request** — a new capability or enhancement.
- **Epic** — a multi-feature effort that ships as one user-facing story.
  See [Epics](#epics) below.
- **Decay / structural debt** — refactor-later observations.
- **Question / support** — questions and support requests.

Before filing, search the target repo's existing issues — open **and**
closed — for the same observation. If it is already on file, comment there
rather than opening a duplicate.

The `authoring-issues-prs` skill (`.claude/skills/authoring-issues-prs/`)
walks this guide's routing and filing procedure and performs the follow-up
steps issue forms cannot (sub-issue links, milestones). It points back at
this file; this file stays the single source of the rules.

### Observation, not work order

An issue records what was **observed**. It does not diagnose, design, or
prescribe a fix. An issue that reads like a work order misleads the
implementer into treating imagination as researched fact.

- Describe what you saw: the concrete behaviour, exact error text or trace,
  where it occurred, the version/commit you checked.
- Do not assert a root cause you did not verify.
- Do not propose an architecture or list implementation steps.

### The uncertainty rule

Every cause statement must be marked:

- `[verified: how]` — you checked; here is how.
- `[unverified]` — you have not verified this.

When you have not verified the cause, this sentence is required:

> I have not verified the cause.

The implementer must inherit your doubt, not a false floor of confidence.

### One issue, one observed problem

If you notice a second suspected problem while writing, do not add it to the
body. If you genuinely suspect it shares a code path, add one line under Open
Questions: `[unverified]: <suspected problem> may share this code path`. Open
a separate issue for it.

### Remove before posting

| What you wrote | What to do instead |
|----------------|-------------------|
| "Root cause is X; fix by doing Y" | Cause: `[unverified]` + observed behaviour only |
| Any sentence starting with "Fix by", "We should", "Refactor", "Add a", "The solution is" | Delete the sentence |
| "Import is probably similarly broken" | One Open Questions line: `[unverified]: import may share this path` |
| A cause asserted without a `[verified]` or `[unverified]` marker | Add the marker; add "I have not verified the cause" if unverified |
| An "Additional context" section that introduces new problems | Open a separate issue |
| Implementation steps (a numbered list of code changes) | Remove entirely |

### Epics

An epic is not just a bigger issue. It is the unit that answers two
questions nothing else answers: **what story does this tell a user**, and
**does it ship as a whole**. File one with the Epic form and:

- **Write "What changes for the user" at epic creation**, before any code
  exists. It becomes the release-notes highlight for the whole epic, so the
  release editor verifies it against what landed instead of reconstructing
  intent from merged PRs.
- **Link children as native GitHub sub-issues, not markdown task lists.**
  Sub-issues make the grouping queryable (parent, children, and progress
  are API fields); a checklist is prose. Issue forms cannot create the link
  at filing time — use the issue sidebar ("Create sub-issue" / "Add
  existing issue") or the sub-issues API after filing. The
  `authoring-issues-prs` skill performs this mechanically.
- **If the epic ships atomically** (no release may be cut mid-epic), make
  that queryable too. Preferred: assign the epic and its children to the
  **milestone** named for the target release — "safe to cut" then reduces
  to "no open issues in that milestone". Milestones are per-repo and
  one-per-issue; for a cross-repo epic the milestone lives in the repo
  where the release is cut. Fallback: apply the **`ships-atomically`
  label** when no target release is named yet, or in the repos of a
  cross-repo epic that do not cut the release. The form's yes/no field
  records intent; only the milestone or label is what release tooling can
  query.

Existing epics tracked as hand-written checklists need no migration.

## Pull requests

Every PR must have at least one associated issue. If the work has no issue
yet, a bug found in the wild or an opportunistic cleanup, create the issue
first, then open the PR with `Closes #N` (or `Refs #N`) in the body. A single
PR may close multiple issues (`Closes #A, closes #B`); the rule is "no orphan
PRs", not "one PR per issue". Trivial exceptions: pure typo fixes and
automated dependency bumps (Renovate) may skip the issue.

Mark a commit breaking (`feat!:` / `BREAKING CHANGE:`) only under the
breaking-change policy in `CLAUDE.md`: the change must break the operator
surface (env var, config file, CLI flag, deployment layout, on-disk state)
or the public library interface, assessed against the **last stable
release**, not the previous commit. MCP tool-surface changes are not
breaking on their own.

State what the PR deliberately does **not** do, with each deferral's tracking
issue. A change that says what it left out is easier to trust than one that
appears to have found nothing.

Run a local code-review pass on the cumulative diff before `gh pr create`.
Code without matching docs is incomplete; check `README.md`, the `docs/`
site, `docs/design/`, and inline docstrings.

## Releases

Merging is not releasing. When a release is cut, and from where, is
governed by the release model in `CLAUDE.md`: releases normally come
straight from a quiescent trunk, and a short-lived `release/X.Y` branch
is the exception tool for excluding unfinished work or patching a
shipped release. The ships-atomically signal recorded on epics
(milestone preferred, label fallback; see [Epics](#epics)) is the input
that judgement consumes: an open atomic epic with unclosed children
means the release comes from before it started, or waits.

## Where to send fixes

- **Library-level fix** (anything you'd change in `fastmcp_pvl_core`): open a
  PR on `pvliesdonk/fastmcp-pvl-core`. After merge + release, bump
  `fastmcp-pvl-core` in this project's `pyproject.toml`. Copier update alone
  won't pick it up unless the template's version constraint in
  `pyproject.toml.jinja` is also bumped.
- **Template-level fix** (anything template-owned: `Dockerfile`, workflows,
  `server.py` skeleton, `CLAUDE.md` sections): open a PR on
  `pvliesdonk/fastmcp-server-template`. After merge + release, this project
  gets the fix on the next weekly `copier update` cron, or dispatch the
  workflow manually.
- **Domain-only fix** (anything inside a `DOMAIN-*`, `CONFIG-*`, or
  `PROJECT-*` sentinel block, `tools.py`, `resources.py`, `prompts.py`,
  `domain.py`, `tests/`): PR on this repo directly.
