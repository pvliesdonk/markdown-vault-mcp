---
type: Reference
title: Open Knowledge Format v0.3
description: Rules of OKF v0.3 relevant to bundle detection and planned alignment
subject_version: "0.3 (spec commit 527e81af, 2026-11-18)"
valid_for: "OKF 0.3 at commit 527e81af"
generated:
  by: process:researching-references
  at: 2026-11-20T11:23:20+00:00
verified:
  - by: process:researching-references-refute
    at: 2026-11-20T11:23:20+00:00
stale_after: 2027-11-20
status: stable
sources:
  - id: spec
    title: Open Knowledge Format v0.3, SPEC.md at commit 527e81af
    resource: https://github.com/open-knowledge-format/open-knowledge-format/blob/527e81af/SPEC.md
    accessed: 2026-11-20
---

# Open Knowledge Format v0.3

This reference records the pinned specification, not a claim that this server
implements all of it. For the previous text see [v0.2](okf-v0.2.md).

- A bundle may declare `okf_version` in root `index.md`; an unknown version should be consumed best-effort, not refused (§12). [source: spec] [pins: tests/test_okf.py::TestOkfDetector::test_declared_auto_active, tests/test_okf.py::TestOkfDetector::test_unknown_version_warns_once_and_detects]
- A non-reserved Markdown file with non-empty `type` is a concept; untyped files do not affect conformance and dot-paths are outside the bundle (§3.1–3.2, §11.1). [source: spec]
- Frontmatter uses YAML 1.2 core mappings; unknown extension keys must be preserved by producers (§4, §4.1). [source: spec]
- Only current verification events determine trust; date-only timestamps mean the start of the day in UTC (§5, §5.3). [source: spec]
- Links and path fields resolve from the bundle root when prefixed with `/`, otherwise from the containing directory; outside-root targets are external. Earlier or undeclared versions may use a root fallback for unresolved bare path fields (§6, §6.2). [source: spec]
- Actors may use any scheme, with only `human:` giving human-reviewed trust (§7). [source: spec]
- Root index frontmatter may contain bundle metadata and extensions and must survive regeneration; `log.md` has no frontmatter (§8.1, §9). [source: spec]
- Conformance requires frontmatter blocks to parse as mappings and reserved files to follow their rules (§11.1). [source: spec]

## Server scope

The server recognizes 0.1, 0.2 and 0.3 declarations and keeps unknown
versions active with a warning. Recognition alone does not implement the
other v0.3 rules above: path resolution, trust, timestamps, YAML parsing and
conformance alignment remain in the planned V-slices. See the
[implementation design](../design.md#okf-read-semantics-phase-1-960).
