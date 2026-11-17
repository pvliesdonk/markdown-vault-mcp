---
type: Reference
title: Regex substitution timeout
description: Operation-wide matching deadlines and replacement callback coverage.
subject_version: "regex 2025.9.1"
valid_for: "regex 2025.9.1 and later"
generated:
  by: process:researching-references
  at: 2026-11-16T00:00:00Z
verified:
  - by: process:researching-references-refute
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
`TimeoutError` when callbacks take too long. [source: regex-readme] On macOS with regex 2026.11.15 and Python 3.14, a sleeping callback did not trigger that timeout in a four-match probe; a CPU-bound callback did. Treat the sleep example as vendor documentation rather than a wall-clock guarantee.
[pins: tests/test_bulk_replace.py::test_regex_timeout_covers_callback, tests/test_bulk_replace.py::test_regex_timeout_is_bounded]

Version 2025.9.1 provides CPython 3.14 wheels. [source: regex-release]

[^regex-readme]: [regex README, Timeout](https://github.com/mrabarnett/mrab-regex#timeout), accessed 2026-11-16.
[^regex-release]: [regex 2025.9.1 files](https://pypi.org/project/regex/2025.9.1/#files), accessed 2026-11-16.
