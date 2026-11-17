---
type: Reference
title: Windows CRT byte-range locks
description: Windows nonblocking byte locks and errors used by the built-in writer lock.
subject_version: "Python 3.13 and Microsoft C runtime (MSVC 170)"
valid_for: "Python 3.x and MSVC 170"
generated:
  by: process:researching-references
  at: 2026-11-17T03:49:34+00:00
verified:
  - by: process:researching-references
    at: 2026-11-17T03:49:34+00:00
stale_after: 2027-05-17T03:49:34+00:00
status: stable
sources:
  - id: python
    title: Python msvcrt file operations
    resource: https://docs.python.org/3.13/library/msvcrt.html#msvcrt.locking
    accessed: 2026-11-17
  - id: microsoft
    title: Microsoft CRT _locking
    resource: https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170
    accessed: 2026-11-17
---

# Windows CRT byte-range locks

This covers the Windows API used by `stores/_writer_lock.py`; POSIX locks and network filesystem guarantees are outside its scope.

- `locking` begins at the current file position and locks `nbytes`, possibly beyond EOF; an unlock must name the previously locked range. [source: python] [source: microsoft] [pins: tests/test_stores_windows_writer_lock.py::test_windows_refcounts_and_unlocks_same_range]
- `LK_NBLCK` fails immediately if the bytes cannot be locked. Microsoft identifies `EACCES` as a locking violation, while `EBADF` indicates a bad descriptor. [source: python] [source: microsoft] [pins: tests/test_stores_windows_writer_lock.py::test_windows_contention_refuses_callers, tests/test_stores_windows_writer_lock.py::test_windows_other_errors_skip]
- Locking prevents other processes accessing the locked bytes; Microsoft's example opens a file read-only and locks its bytes. [source: microsoft] [pins: tests/test_stores_windows_writer_lock.py::test_windows_read_only_file_can_lock]

The project reserves a byte beyond the PID field, leaving that field readable for diagnostics; see [the writer-lock design](../design.md). Native Windows behavior still needs an integration run; the pins use a fake API on POSIX.
