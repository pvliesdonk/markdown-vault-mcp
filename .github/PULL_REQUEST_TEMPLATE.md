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

## Design

<!-- For a feature, include the spec agreed in session or offline here.
     Fold longer text in <details><summary>Approved design</summary> ...
     </details>, with blank lines around the Markdown body.
     Small bugs and enhancements may leave this section empty.
     If the complete PR body would exceed 65,536 characters, attach the
     spec .md in GitHub's UI and link it here, keeping a decision summary.
     Attachment is a human step; do not use undocumented upload APIs.
     Specs and plans under docs/superpowers/ stay local and gitignored. -->

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
- [ ] Ported enduring decisions the spec explains but the code and
      `docs/design/` do not now show, or explained why none needed porting.

**Rule: code without matching docs is incomplete.**
