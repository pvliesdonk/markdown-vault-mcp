# Contributing

Thanks for contributing. This guide covers how to file good issues and pull
requests, and where to send different kinds of fixes. It applies to both
human contributors and automated agents.

## Filing issues

Use the issue templates in `.github/ISSUE_TEMPLATE/`:

- **Bug report** — something isn't working as expected.
- **Feature request** — a new capability or enhancement.
- **Decay / structural debt** — refactor-later observations.
- **Question / support** — questions and support requests.

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

## Pull requests

Every PR must have at least one associated issue. If the work has no issue
yet, a bug found in the wild or an opportunistic cleanup, create the issue
first, then open the PR with `Closes #N` (or `Refs #N`) in the body. A single
PR may close multiple issues (`Closes #A, closes #B`); the rule is "no orphan
PRs", not "one PR per issue". Trivial exceptions: pure typo fixes and
automated dependency bumps (Renovate) may skip the issue.

State what the PR deliberately does **not** do, with each deferral's tracking
issue. A change that says what it left out is easier to trust than one that
appears to have found nothing.

Run a local code-review pass on the cumulative diff before `gh pr create`.
Code without matching docs is incomplete; check `README.md`, the `docs/`
site, `docs/design/`, and inline docstrings.

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
