# Review instructions

<!-- DOMAIN-REVIEW-START -->
<!-- Add project-specific review rules here. Kept across copier update. -->
<!-- DOMAIN-REVIEW-END -->

<!-- ===== TEMPLATE-OWNED BELOW — DO NOT EDIT; OVERWRITTEN ON COPIER UPDATE ===== -->

## Don't reproduce CI

CI already enforces the full gate: ruff (lint + format), mypy, the pytest
matrix, dependency audit, secret scan, Vale prose, and — when enabled — the
structural gate. Do **not** report findings those checks already gate, and do
**not** re-run the gate. When you need a check's result, read it from CI
(`gh run view`) instead of running it yourself.

## Investigate narrowly

You may run a single targeted command to confirm one specific hypothesis (for
example, one test or `mypy` on one file). This is for verifying a finding — not
for re-executing the suite.

## Focus

Report correctness bugs, security issues, and regressions introduced by this
diff. Behavior claims need a `file:line` citation in the source, not an
inference from naming.

## Template-owned lines

When `.copier-answers.yml` exists, this project tracks a template: in a
file that carries sentinel blocks (`NAME-START` … `NAME-END` comments), only the
content inside those blocks is the project's, and every other line is
re-rendered by the next template update. When a hunk adds or changes such
a line outside every block, report it: name the file and lines, and point
at the block in that file where the content belongs. A pull request may
carry such a line on purpose only with a Decay issue that names it; say so
when the pull request body cites none.

## Converge

After the first review of a PR, suppress repeat nits and post Important
findings only. A one-line fix should not reach round seven on style.
