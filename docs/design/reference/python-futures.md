---
type: Reference
title: Python Future completion and cancellation
description: Callback ordering and cancellation rules used by the index build lifecycle.
subject_version: "Python 3.14 documentation; CPython v3.14.0 source"
valid_for: "Python 3.11–3.14 concurrent.futures public API; implementation ordering pinned to CPython v3.14.0"
generated:
  by: process:researching-references
  at: 2026-09-14
verified:
  - by: process:researching-references
    at: 2026-09-14
stale_after: 2027-03-14
status: stable
sources:
  - id: python-docs
    title: concurrent.futures Future objects
    resource: https://docs.python.org/3.14/library/concurrent.futures.html#future-objects
    accessed: 2026-09-14
  - id: cpython-source
    title: CPython v3.14.0 concurrent.futures implementation
    resource: https://github.com/python/cpython/blob/v3.14.0/Lib/concurrent/futures/_base.py
    accessed: 2026-09-14
---

# Python Future completion and cancellation

## Scope

The index build lifecycle depends on completion ordering, cancellation and
exception delivery. Executor pools, process serialization and asyncio Futures
are outside this reference.

## Claims

- `result(timeout)` raises the callable's exception, `CancelledError` for
  cancellation, or `TimeoutError` when its wait expires. A callable may itself
  raise `TimeoutError`; its type alone cannot distinguish those cases.
  [source: python-docs]
  [pins: tests/test_build_mutation_outcomes.py::test_build_timeout_error_is_failure_not_wait_timeout]
- Cancellation cannot stop running work. Executors claim execution with
  `set_running_or_notify_cancel`; a false return means they must skip the work.
  [source: python-docs]
  [pins: tests/test_build_mutation_outcomes.py::test_pending_build_never_authorizes_mutation]
- Completion callbacks run in registration order, and registering on an already
  completed or cancelled Future invokes the callback immediately. The API does
  not promise a dedicated callback thread. Callback `Exception`s are logged and
  ignored; callback `BaseException` behavior is undefined. [source: python-docs]
- CPython publishes the finished state and wakes result waiters before invoking
  callbacks in `set_result` and `set_exception`. A result waiter therefore does
  not establish that a completion callback has finished. `cancel` also publishes
  cancellation before invoking callbacks. [source: cpython-source]
  [pins: tests/test_mutation_build_finalization.py::test_mutation_waits_for_build_finalization]
- CPython invokes callbacks synchronously on completion/cancellation or immediate
  registration. It releases the Future's condition first, but does not release
  locks held by its caller. Calling cancellation under an executor's submission
  lock can therefore deadlock a callback which submits more work.
  [source: cpython-source]
  [pins: tests/test_writer_cancellation_callbacks.py::test_crash_cancellation_callbacks_can_submit_without_deadlock]

## Project contract

The [build lifecycle](../design.md#build-lifecycle-ownership-1483) completes its
public Future after marker and readiness publication. The writer notifies
cancelled callers outside its submission lock. These are project guarantees
built on the rules above, not stronger claims about arbitrary Futures.

## Not covered

Callbacks supplied by callers must not synchronously wait for another job on the
same single worker. Free-threaded interpreter memory ordering is not assessed.
