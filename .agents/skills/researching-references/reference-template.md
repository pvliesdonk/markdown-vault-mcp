---
type: Reference
title: <Subject, as a reader would look it up>
description: <What was researched, one sentence>
subject_version: "<version or line the claims were checked against, e.g. 1.9>"
valid_for: "<expiry condition in the subject's terms, e.g. Obsidian 1.x>"
generated:
  by: process:researching-references
  at: <YYYY-MM-DD>
stale_after: <YYYY-MM-DD, six months for a moving target, twelve for a frozen spec>
status: stable
sources:
  - id: <short-id>
    title: <Page or document title>
    resource: <https://...>
    accessed: <YYYY-MM-DD>
---

# <Title>

<!-- One paragraph: what this reference covers, what it deliberately leaves
out, and which module(s) in this repository depend on it. -->

## Scope

- Covers: ...
- Does not cover: ...
- Depended on by: `src/<module>.py` (...), `docs/design/design.md` § ...

## Claims

<!-- One claim per behaviour. Each carries a marker: [source: id],
[observed: how], or [unverified], plus [pins: tests/x.py::test_y] where a
test in this repository asserts the project honours it. Group by facet. -->

### <Facet>

- <Claim.> [source: short-id] [pins: tests/test_x.py::test_y]
- <Claim.> [observed: `fixture.md` under `tests/fixtures/`, run with ...]
- <Claim.> [unverified] <What would verify it.>

## Where this project departs from the subject

<!-- Deliberate divergences, each linking the design-doc section that
decides it. Empty is a valid answer; say so. -->

## Not covered

<!-- Facets the code touches that no claim settles yet, with what would
settle them. -->
