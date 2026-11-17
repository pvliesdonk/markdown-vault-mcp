---
type: Reference
title: Replacement worker
description: Python process timeouts and regular expression replacement semantics.
subject_version: "Python 3.14"
valid_for: "Python 3.14 subprocess.run, re.Match.expand and io.TextIOWrapper"
generated:
  by: process:researching-references
  at: 2026-11-16T00:00:00Z
verified:
  - by: process:researching-references-refute
    at: 2026-11-16T00:00:00Z
stale_after: 2027-05-16T00:00:00Z
status: stable
sources:
  - id: subprocess
    title: Python 3.14 subprocess.run
    resource: https://docs.python.org/3.14/library/subprocess.html#subprocess.run
    accessed: 2026-11-16
  - id: match-expand
    title: Python 3.14 re.Match.expand
    resource: https://docs.python.org/3.14/library/re.html#re.Match.expand
    accessed: 2026-11-16
  - id: text-io
    title: Python 3.14 TextIOWrapper newline translation
    resource: https://docs.python.org/3.14/library/io.html#io.TextIOWrapper
    accessed: 2026-11-16
---

# Replacement worker

`subprocess.run` kills and waits for the child when its timeout expires, then raises `TimeoutExpired`. Process creation itself cannot be interrupted on many platforms. [source: subprocess] [pins: tests/test_bulk_replace.py::test_hostile_regex_runs_in_bounded_process]

`Match.expand` performs the same backslash substitutions as `sub`, including numeric and named references. [source: match-expand] Fragment expansion must preserve the escape boundaries, including a backslash followed by a newline; the fixture compares fragments against whole-template `re.sub` on Python 3.14. [observed: tests/test_bulk_replace.py::test_regex_replacement_fragments_match_stdlib] [pins: tests/test_bulk_replace.py::test_regex_replacement_fragments_match_stdlib]

With `TextIOWrapper` configured with `newline=None`, writing translates `\n` to the platform line separator. Binary UTF-8 writes bypass text newline translation. [source: text-io] The simulated Windows regression pins preservation of CRLF bytes. [pins: tests/test_bulk_replace.py::test_windows_crlf_write_preserves_bytes]
