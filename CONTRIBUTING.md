# Contributing

These rules govern every issue and pull request, and where each kind of fix
goes. File and open both with the `authoring-issues-prs` skill; where the
skill and this file disagree, this file wins.

## Filing issues

File every issue with a form from `.github/ISSUE_TEMPLATE/`. Before filing,
search the target repo's issues, open **and** closed, for the same
observation; if it is on file, comment there instead.

### Observation, not work order

Describe what you observed: the concrete behaviour, the exact error text or
trace, where it occurred, and the version or commit you checked. Leave out
diagnosis, architecture proposals and implementation steps.

### The uncertainty rule

Mark every cause statement `[verified: how]` or `[unverified]`. When the
cause is unverified, include this sentence:

> I have not verified the cause.

### One issue, one observed problem

Open a separate issue for a second suspected problem. If it may share a code
path, add one line under Open Questions:
`[unverified]: <suspected problem> may share this code path`.

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

An **epic** is a story: a parent issue labelled `epic`, with its children
linked as native sub-issues. A **package** is the payload of one release
cut: a milestone titled `NNN content-name`, and the current package is the
lowest open ordinal. Before charting, refining or revisiting either, or
`docs/design/roadmap.md`, read the `roadmapping` skill.

## Agent-authored posts

These rules cover everything an agent posts through a human's credentials:
issue bodies and comments, PR descriptions and comments, review summaries
and inline replies.

**Writing.** End every such post with this footer. Keep its first words
exactly as shown, because later readers grep for them, and name the product
you actually are, never another one:

```markdown
---
_Agent-authored: written by [Claude Code](https://claude.ai/code) under
this account's credentials. Analysis and proposal, not a decision by the
account holder._
```

Write as a proposer; the account holder decides in a reply. When the post is
the account holder's words dictated verbatim, keep the footer and say so.
Commits keep their `Co-Authored-By:` trailer instead.

**Reading.** Before treating a post under a human's name as their decision,
check it for the `Agent-authored:` marker, since it may be an earlier
session's output, including yours. Treat a marked post as a proposal until a
human's reply adopts it.

## Pull requests

Follow `AGENTS.md` for issue linking ("PR Discipline"), the gates and
self-review before every push ("Hard PR Acceptance Gates"), and the
breaking-change policy, and fill every section of
`.github/PULL_REQUEST_TEMPLATE.md`. Squash-merge issue and feature PRs;
merge an integration branch's final PR with a merge commit.

## Releases

Merging is not releasing. When and from where to cut: the `releasing` skill.

## Where to send fixes

- **Library-level fix** (anything in `fastmcp_pvl_core`): open a PR on
  `pvliesdonk/fastmcp-pvl-core`, where each public name lives in a private
  `_<area>.py` module. Leave this project's `fastmcp-pvl-core` constraint in
  `pyproject.toml` alone; it is template-owned. After the library release,
  if the constraint admits it, run
  `uv lock --upgrade-package fastmcp-pvl-core` and commit `uv.lock`;
  otherwise open a template PR that bumps the constraint in
  `pyproject.toml.jinja`.
- **Template-level fix** (anything template-owned: `Dockerfile`, workflows,
  `server.py` skeleton, `AGENTS.md` sections, template documentation pages):
  open a PR on `pvliesdonk/fastmcp-server-template` against the rendered
  file's path with `.jinja` appended (`docs/deploy/docker.md.jinja`), or the
  same path for a file copied verbatim, such as this one. The weekly
  `copier update` workflow brings the released fix here; dispatch it to get
  it sooner.
- **Domain-only fix** (anything inside a `DOMAIN-*`, `CONFIG-*` or
  `PROJECT-*` sentinel block, `tools.py`, `resources.py`, `prompts.py`,
  `domain.py`, `tests/`): open a PR on this repo.
