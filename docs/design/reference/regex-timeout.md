---
type: Reference
title: Regex substitution timeout
subject_version: "regex 2025.9.1"
valid_for: "regex 2025.9.1 and later"
generated:
  by: process:researching-references
  at: 2026-11-16T00:00:00Z
stale_after: 2027-05-16T00:00:00Z
status: stable
sources:
  - id: regex-readme
    title: regex README, Timeout
    resource: https://github.com/mrabarnett/mrab-regex#timeout
    accessed: 2026-11-16
  - id: regex-release
    title: regex 2025.9.1 files
    resource: https://pypi.org/project/regex/2025.9.1/#files
    accessed: 2026-11-16
---

# Regex substitution timeout

The `regex` matching and substitution timeout applies to the entire operation,
including time spent in the replacement callback. The documented example raises
`TimeoutError` when callbacks take too long. [^regex-readme]

Version 2025.9.1 provides CPython 3.14 wheels. [^regex-release]

[^regex-readme]: [regex README, Timeout](https://github.com/mrabarnett/mrab-regex#timeout), accessed 2026-11-16.
[^regex-release]: [regex 2025.9.1 files](https://pypi.org/project/regex/2025.9.1/#files), accessed 2026-11-16.
