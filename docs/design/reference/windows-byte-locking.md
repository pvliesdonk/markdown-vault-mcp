---
type: Reference
title: Windows CRT byte-range locks
description: Windows nonblocking byte locks, their errors, release on exit and file translation mode, as the built-in writer lock uses them.
subject_version: "CPython 3.14 (msvcrt, os.open), Python 3.15 docs, Microsoft UCRT (MSVC 170) and Win32 fileapi"
valid_for: "Python 3.x on Windows with the Universal CRT"
generated:
  by: process:researching-references
  at: 2026-11-28T12:00:00+00:00
verified:
  - by: process:researching-references
    at: 2026-11-28T12:00:00+00:00
stale_after: 2027-05-28T12:00:00+00:00
status: stable
sources:
  - id: python
    title: Python msvcrt, file operations
    resource: https://docs.python.org/3/library/msvcrt.html
    accessed: 2026-11-28
  - id: cpython-msvcrt
    title: CPython 3.14 PC/msvcrtmodule.c
    resource: https://github.com/python/cpython/blob/3.14/PC/msvcrtmodule.c
    accessed: 2026-11-28
  - id: cpython-posix
    title: CPython 3.14 Modules/posixmodule.c, os.open
    resource: https://github.com/python/cpython/blob/3.14/Modules/posixmodule.c
    accessed: 2026-11-28
  - id: crt-locking
    title: Microsoft CRT _locking
    resource: https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170
    accessed: 2026-11-28
  - id: crt-text-binary
    title: Microsoft CRT text and binary mode file I/O
    resource: https://learn.microsoft.com/en-us/cpp/c-runtime-library/text-and-binary-mode-file-i-o?view=msvc-170
    accessed: 2026-11-28
  - id: lockfile
    title: Win32 LockFile
    resource: https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfile
    accessed: 2026-11-28
  - id: lockfileex
    title: Win32 LockFileEx
    resource: https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex
    accessed: 2026-11-28
---

# Windows CRT byte-range locks

The consumer is `stores/_writer_lock.py`; the project policy is the writer
lock in [design.md](../design.md) ("One process writes a base path at a
time"). POSIX `flock` and network filesystems are out of scope.

## Locking

- `msvcrt.locking(fd, mode, nbytes)` locks `nbytes` from the current file
  position and may extend past the end of the file; `_locking` calls it "all
  locking or unlocking begins at the current position of the file pointer",
  and "it's possible to lock bytes past end of file". A region being unlocked
  must have been locked before, and adjacent regions are not merged.
  [source: python] [source: crt-locking]
  [pins: tests/test_stores_windows_writer_lock.py::test_windows_refcounts_and_unlocks_same_range]
- `LK_NBLCK` returns an error at once when the bytes cannot be locked
  (`_LK_LOCK` instead retries once a second, ten times). [source: python]
  [source: crt-locking]
- `_locking`'s errors are `EACCES`, "Locking violation (file already locked
  or unlocked)", `EBADF`, "Invalid file descriptor", `EDEADLOCK` (only for
  `_LK_LOCK` and `_LK_RLCK`) and `EINVAL`. [source: crt-locking]
  [pins: tests/test_stores_windows_writer_lock.py::test_windows_lock_error_mapping]
- CPython raises a failed `_locking` as `PyErr_SetFromErrno(PyExc_OSError)`,
  so the error arrives as an `OSError` carrying the CRT `errno`;
  `EACCES` therefore arrives as its `PermissionError` subclass.
  [source: cpython-msvcrt] [source: python]
  [pins: tests/test_stores_windows_writer_lock.py::test_windows_lock_error_mapping]
- "Locking bytes in a file prevents access to those bytes by other
  processes": a Windows byte lock is mandatory, unlike `flock`. The Win32
  locks say the same of an exclusive lock, which "denies all other processes
  both read and write access to the specified region of the file".
  [source: crt-locking] [source: lockfileex]
- Microsoft's `_locking` example opens its file `_O_RDONLY` and locks it,
  and the Win32 lock calls need a handle with only `GENERIC_READ` or
  `GENERIC_WRITE`: a read-only descriptor can be locked. [source: crt-locking]
  [source: lockfile] Not observed natively: the project's tests use a fake
  `msvcrt` on POSIX. [unverified: needs a run on Windows]
- `_locking` advises that "regions should be locked only briefly and should
  be unlocked before closing a file or exiting the program". The project
  departs from this on purpose: a writer holds its lock for the store's
  lifetime and relies on the release at exit described below, as the design
  section above records. [source: crt-locking]

## Release when a process ends

- "If a process terminates with a portion of a file locked or closes a file
  that has outstanding locks, the locks are unlocked by the operating system.
  However, the time it takes for the operating system to unlock these locks
  depends upon available system resources." Until then, "access to these
  files may be denied". Neither page gives a bound. [source: lockfile]
  [source: lockfileex]
- That the CRT's `_locking` is built on these Win32 calls, and so inherits
  this delayed release, is not stated on either Microsoft page.
  [unverified: the UCRT source ships with the Windows SDK and was not read]

## Translation mode

- A CRT file opened without `_O_BINARY` or `_O_TEXT` takes the default
  translation mode, which starts as text mode (`_O_TEXT`); `_setmode`
  changes a descriptor's mode after it is open. [source: crt-text-binary]
- CPython's `os.open` adds only `O_NOINHERIT` to the caller's flags on
  Windows before calling `_wopen`, so it does not itself request binary
  mode. [source: cpython-posix] Whether CPython changes the CRT's default
  mode at startup was not checked, so the project sets binary mode itself.
  [unverified: CPython startup code not read]
- `msvcrt.setmode(fd, os.O_BINARY)` sets a descriptor to binary mode, and
  CPython raises a failure as `OSError` from `errno`. [source: python]
  [source: cpython-msvcrt]
  [pins: tests/test_stores_windows_writer_lock.py::test_windows_refcounts_and_unlocks_same_range]

## Not covered

How the lock behaves over SMB or a virtual machine's shared folder, and how
`os.path.realpath` names a lock file on a case-sensitive Windows directory.
Every claim above was checked against documentation and source only; none was
reproduced on Windows.
