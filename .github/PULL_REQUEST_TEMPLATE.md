## Closes / Refs

Closes #N  (or `Refs #N` if not closing)

> **No orphan PRs.** Create the issue first if none exists. Pure typo fixes
> and Renovate dependency bumps excepted.

## What & why

One or two sentences, in observation terms: what changed, and why.

## What this PR deliberately does NOT do

List each deferral with its tracking issue number. A change that says what
it deliberately did not do is easier to trust than one that appears to have
found nothing.

- (deferral): #N

## Local review

- [ ] Ran a local code-review pass on the cumulative diff before `gh pr create`.
- [ ] Any commit carrying `!` breaks an operator or library surface that
      existed at the **last stable release** (see the breaking-change policy
      in `AGENTS.md`) — MCP tool-surface changes alone do not earn a `!`.

## Docs impact

- [ ] `README.md`
- [ ] `docs/` site pages
- [ ] `docs/design/`
- [ ] Inline docstrings

**Rule: code without matching docs is incomplete.**
