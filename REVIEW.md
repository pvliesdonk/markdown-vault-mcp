# Review instructions

<!-- DOMAIN-REVIEW-START -->
## Changes to a governed subsystem

For a diff that touches a subsystem listed in `docs/design/north-stars.md`,
read its north star and classify the diff's choices as the
`working-under-a-north-star` skill does, then report:

- a pull request whose "Governing north star" section names no north star or
  no passage;
- a choice that a passage of the north star, or a decision recorded on its
  reopening issue, rules out, or that leaves the road ahead it sets unclear:
  a blocker, because the change waits for the north star to be reopened,
  whatever else the review finds;
- a choice the north star is silent on that the pull request does not state
  as "The north star is silent on X; I chose Y because Z".

A choice the north star does not mention is not a finding when the pull
request states it; check that the stated reason holds instead.
<!-- DOMAIN-REVIEW-END -->

## Don't reproduce CI

CI already enforces the full gate: ruff (lint + format), mypy, the pytest
matrix, secret scan, Vale prose, and — when enabled — the structural gate. It
also runs a dependency audit that reports without gating. Do **not** report
findings those checks already cover, and do **not** re-run them. When you need a check's result, read it from CI
(`gh run view`) instead of running it yourself.

## Investigate narrowly

You may run a single targeted command to confirm one specific hypothesis (for
example, one test or `mypy` on one file). This is for verifying a finding — not
for re-executing the suite.

## Focus

Report correctness bugs, security issues, and regressions introduced by this
diff. Behavior claims need a `file:line` citation in the source, not an
inference from naming.

## Converge

After the first review of a PR, suppress repeat nits and post Important
findings only. A one-line fix should not reach round seven on style.
