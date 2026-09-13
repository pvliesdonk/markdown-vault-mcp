# Contributing

Thanks for contributing. This guide covers how to file good issues and pull
requests, and where to send different kinds of fixes. It applies to both
human contributors and automated agents.

## Filing issues

Use the issue templates in `.github/ISSUE_TEMPLATE/`:

- **Bug report** — something isn't working as expected.
- **Feature request** — a new capability or enhancement.
- **Epic** — a multi-feature effort that ships as one user-facing story.
  See [Epics, packages and the roadmap](#epics-packages-and-the-roadmap) below.
- **Research** — a question whose answer changes what happens next, with
  an appetite agreed before starting.
- **Decay / structural debt** — refactor-later observations.
- **Question / support** — questions and support requests.

Before filing, search the target repo's existing issues — open **and**
closed — for the same observation. If it is already on file, comment there
rather than opening a duplicate.

The `authoring-issues-prs` skill (`.agents/skills/authoring-issues-prs/`)
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

### Epics, packages and the roadmap

An **epic** is a story, represented by a parent issue labelled `epic`.
A **package** is the payload of one release cut, represented by a milestone.
An issue may have both: its story and its cut are independent.

File epics with the Epic form. Write "Done when" as an outcome before
decomposing it, and freeze it through refinement. If the outcome needs to
change, record the reason in the roadmap before revising it. "What changes
for the user" is a separate, editable release-notes highlight.

Link children as native GitHub sub-issues. Every epic starts with a
`refinement` sub-issue pointing at its roadmap entry and "Done when";
close it when feature issues plausibly cover that outcome. An epic whose
only open child is its refinement task is an idea, not executable work.
Research issues answer consequential unknowns within an agreed appetite;
closing one updates the roadmap argument with its evidence.

Name packages `NNN content-name`, using gaps such as `010`, `020`,
`030`. The current package is the lowest open ordinal; sort the
Milestones page alphabetically to see title order. Kind (major, minor,
patch) is intent in the index; versions come from the release tool.
Membership commits an issue to shipping in that cut. No milestone means
backlog: do not create `Backlog` or `Future` milestones.

Epics normally have no milestone because linked children can inherit it.
For an epic that ships atomically in one package, assign the epic and its
children to that package. For cross-repo epics, package membership is local
to the repository cutting the release; use `ships-atomically` in other
repositories or when no package is committed yet. Release Prepare warns
about open items (issues and PRs) in the current package and open atomic
epics. It does not block a deliberate cut. Keep the release PR itself out
of the package.

After a stable default-branch release, the workflow records the computed
version in the package title, removes open items to backlog with a job
summary, then closes the milestone. Failures warn and leave it open for
retry. Re-commit leftovers deliberately. Branch releases and prereleases
leave trunk packages alone.

`docs/design/roadmap.md` holds direction, intended package order and
known unknowns, with `stated`, `derived` or `evidenced` provenance.
GitHub holds status and native issue dependencies. Read the
`roadmapping` skill before charting, refining or revisiting these objects.
It describes the evidence rule and the ready-feature handoff.

A `breaking` label identifies a known break to an existing operator or
library contract, assessed against the last stable release. Merely
touching that surface does not earn the label. There is no breaking-PR
merge gate: hold implementation or merge when batching is useful, or ship
the compatible half first and file the breaking half separately.

## Pull requests

Every PR must have at least one associated issue. If the work has no issue
yet, a bug found in the wild or an opportunistic cleanup, create the issue
first, then open the PR with `Closes #N` (or `Refs #N`) in the body. A single
PR may close multiple issues (`Closes #A, closes #B`); the rule is "no orphan
PRs", not "one PR per issue". Trivial exceptions: pure typo fixes and
automated dependency bumps (Renovate) may skip the issue.

Mark a commit breaking (`feat!:` / `BREAKING CHANGE:`) only under the
breaking-change policy in `AGENTS.md`: the change must break the operator
surface (env var, config file, CLI flag, deployment layout, on-disk state)
or the public library interface, assessed against the **last stable
release**, not the previous commit. MCP tool-surface changes are not
breaking on their own.

State what the PR deliberately does **not** do, with each deferral's tracking
issue. A change that says what it left out is easier to trust than one that
appears to have found nothing.

Run a local code-review pass on the cumulative diff before `gh pr create` —
the `code-review` skill (`.agents/skills/code-review/SKILL.md`) is the
procedure, and it works with any coding agent. Code without matching docs is
incomplete; check `README.md`, the `docs/` site, `docs/design/`, and inline
docstrings.

The issue is the request; the PR is the resolution. Review a feature's
spec in session or offline, then include the approved spec under the PR's
Design section, folded when long. If it exceeds the body limit, a human
can attach the Markdown file in GitHub's UI; keep a decision summary in
the body and never silently truncate the spec. Use only documented APIs.
Small bugs and enhancements need no invented spec.

`docs/superpowers/` is local, gitignored scratch for specs and plans.
Do not commit new files there; historical tracked files stay as history.
At merge, ask: what did the spec say that the code and `docs/design/`
do not now show? Port enduring decisions to `docs/design/` or an ADR.
The merged PR preserves the feature-level intent, reachable from the
squash commit.

## Releases

Merging is not releasing. When a release is cut, and from where, is
governed by the release model in the `releasing` skill
(`.agents/skills/releasing/SKILL.md`): releases normally come
straight from a quiescent trunk, and a short-lived `release/X.Y` branch
is the exception tool for excluding unfinished work or patching a
shipped release. The ships-atomically signal recorded on epics
(package preferred, label fallback; see [Epics](#epics-packages-and-the-roadmap)) is the input
that judgement consumes: an open atomic epic with unclosed children
means the release comes from before it started, or waits.

## Where to send fixes

- **Library-level fix** (anything you'd change in `fastmcp_pvl_core`): open a
  PR on `pvliesdonk/fastmcp-pvl-core`. After merge + release, bump
  `fastmcp-pvl-core` in this project's `pyproject.toml`. Copier update alone
  won't pick it up unless the template's version constraint in
  `pyproject.toml.jinja` is also bumped.
- **Template-level fix** (anything template-owned: `Dockerfile`, workflows,
  `server.py` skeleton, `AGENTS.md` sections): open a PR on
  `pvliesdonk/fastmcp-server-template`. After merge + release, this project
  gets the fix on the next weekly `copier update` cron, or dispatch the
  workflow manually.
- **Domain-only fix** (anything inside a `DOMAIN-*`, `CONFIG-*`, or
  `PROJECT-*` sentinel block, `tools.py`, `resources.py`, `prompts.py`,
  `domain.py`, `tests/`): PR on this repo directly.
