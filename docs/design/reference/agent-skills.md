---
type: Reference
title: Agent Skills frontmatter
description: The SKILL.md filename and metadata slot, with the server's tolerant OKF read departure.
kind: reference
subject_version: "Agent Skills specification, accessed 2026-11-14"
valid_for: "The unversioned specification as read on 2026-11-14; recheck on a format change"
generated:
  by: process:researching-references
  at: 2026-11-14T09:15:00Z
verified:
  - by: process:researching-references-refute
    at: 2026-11-14T09:15:00Z
stale_after: 2027-05-14
status: stable
sources:
  - id: specification
    title: Agent Skills specification
    resource: https://agentskills.io/specification.md
    accessed: 2026-11-14
---

# Agent Skills frontmatter

## Claims

- A skill directory contains a file named `SKILL.md`, with YAML frontmatter
  followed by Markdown instructions. [source: specification] (§ Directory
  structure, § SKILL.md format)
  [pins: tests/test_okf.py::test_skill_metadata_leaves_ordinary_notes_literal]
- The frontmatter table names `name` and `description` as required fields,
  and `license`, `compatibility`, `metadata`, and `allowed-tools` as optional
  fields. Additional properties belong in `metadata`, described as a mapping
  from string keys to string values. [source: specification] (§ Frontmatter,
  § metadata field)
  [pins: tests/test_okf.py::test_effective_skill_metadata]

## Deliberate consumer departure

The server accepts structured OKF values under `metadata` for exact
`SKILL.md` basenames, including mappings for `generated` and lists for
`sources` and `verified`. Those values exceed the specification's string
mapping; accepting them does not assert strict Agent Skills producer
conformance. The [OKF design](../okf.md#skill-metadata-reads) defines this
read-only projection and its filename-derived `Skill` fallback. Raw YAML and
file bytes stay literal.
[pins: tests/test_okf.py::test_effective_skill_metadata, tests/test_okf.py::test_skill_audit_uses_nested_type]

## Not covered

Name validation, instruction execution, installers and extras blocks are
outside this read-side consumer contract.
