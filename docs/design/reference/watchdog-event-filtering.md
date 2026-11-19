---
type: Reference
title: Watchdog event filtering
description: Mutation subscriptions, native inotify masks and the minimum FSEvents API used by the vault watcher.
kind: reference
subject_version: "watchdog 4.0.2 and 7.1.0; FSEvents API compared with 4.0.0/4.0.1"
valid_for: "The inspected watchdog tags; recheck on a backend API change"
generated:
  by: process:researching-references
  at: 2026-11-14T09:06:00+00:00
verified:
  - by: process:refute-pass
    at: 2026-11-14T09:06:00+00:00
stale_after: 2027-05-14T09:06:00+00:00
status: stable
sources:
  - id: api7
    title: Watchdog 7.1.0 observer and emitter API
    resource: https://github.com/gorakhargosh/watchdog/blob/v7.1.0/src/watchdog/observers/api.py
    accessed: 2026-11-14
  - id: ino4
    title: Watchdog 4.0.2 inotify emitter
    resource: https://github.com/gorakhargosh/watchdog/blob/v4.0.2/src/watchdog/observers/inotify.py
    accessed: 2026-11-14
  - id: ino7
    title: Watchdog 7.1.0 inotify emitter and recursive watch groups
    resource: https://github.com/gorakhargosh/watchdog/blob/v7.1.0/src/watchdog/observers/inotify.py
    accessed: 2026-11-14
  - id: fs400
    title: Watchdog 4.0.0 FSEvents schedule override
    resource: https://github.com/gorakhargosh/watchdog/blob/v4.0.0/src/watchdog/observers/fsevents.py
    accessed: 2026-11-14
  - id: fs401
    title: Watchdog 4.0.1 FSEvents schedule override
    resource: https://github.com/gorakhargosh/watchdog/blob/v4.0.1/src/watchdog/observers/fsevents.py
    accessed: 2026-11-14
  - id: fs7
    title: Watchdog 7.1.0 FSEvents emitter
    resource: https://github.com/gorakhargosh/watchdog/blob/v7.1.0/src/watchdog/observers/fsevents.py
    accessed: 2026-11-18
  - id: fs402
    title: Watchdog 4.0.2 FSEvents schedule override
    resource: https://github.com/gorakhargosh/watchdog/blob/v4.0.2/src/watchdog/observers/fsevents.py
    accessed: 2026-11-14
  - id: changes
    title: Watchdog changelog through 7.1.0
    resource: https://github.com/gorakhargosh/watchdog/blob/v7.1.0/changelog.rst
    accessed: 2026-11-14
---

# Watchdog event filtering

This reference covers the subscriptions used by `_file_watcher.py` and the
[watcher scoping design](../design.md). It does not prescribe overflow recovery
or recursive exclusion policy.

## Claims

- `BaseObserver.schedule(event_filter=...)` passes concrete event classes to
  the emitter; `EventEmitter.queue_event` admits an event when it is an
  instance of an allowed class. [source: api7]
  [pins: tests/test_file_watcher.py::test_start_filters_mutations_on_every_watch]
- The eight file/directory create, modify, move and delete classes admit those
  mutations independently; filtering only file classes would discard directory
  events at the emitter boundary. [source: api7]
  [pins: tests/test_file_watcher.py::test_start_filters_mutations_on_every_watch, tests/test_file_watcher.py::test_mutating_events_schedule]
- On Linux, `InotifyEmitter.on_thread_start` passes the mask derived by
  `get_event_mask_from_filter` to its native watch owner, so this filter changes
  subscriptions before queueing, beyond what the handler can suppress.
  [source: ino4] [source: ino7]
  [pins: tests/test_file_watcher.py::test_inotify_watch_mask_suppresses_reads]
- In both inspected inotify versions, the eight classes retain MODIFY, ATTRIB,
  CREATE, DELETE, MOVED_FROM, MOVED_TO, DELETE_SELF and CLOSE_WRITE, while
  excluding OPEN, CLOSE_NOWRITE and ACCESS. [source: ino4] [source: ino7]
  [pins: tests/test_file_watcher.py::test_inotify_watch_mask_suppresses_reads]
- `DirModifiedEvent` requests CLOSE_WRITE; the emitter converts a file's writable
  close into a parent `DirModifiedEvent` as well as a `FileClosedEvent`, so retaining
  that native bit preserves a notification the existing handler can observe.
  [source: ino4] [source: ino7]
  [pins: tests/test_file_watcher.py::test_inotify_watch_mask_suppresses_reads, tests/test_file_watcher.py::test_mutating_events_schedule]
- The 7.1.0 inotify emitter queues `DirModifiedEvent(parent)` directly after a
  child's create, delete and move (both parents), and also after a writable
  close. Only the first three are adjacent to a child event the handler can
  judge, so only an ignored hidden create, delete or move arms its parent, and
  the next `DirModifiedEvent` for exactly that parent is absorbed once. Every
  other event, a hidden modification included, clears the pending parents, so a
  bare `DirModifiedEvent` still schedules and the `IN_CLOSE_WRITE` signal
  survives; a writable close of a hidden file still schedules, as before. The
  state is at most two parent paths per handler. On macOS with 7.1.0 the
  observed stream for a hidden directory created and removed in a folder placed
  `modified <folder>` directly after each hidden `created` and `deleted` of that
  directory; other watchdog versions and FSEvents orderings were not checked,
  and a notice that arrives non-adjacent schedules a reindex, as it did before.
  [source: ino7]
  [pins: tests/test_file_watcher.py::test_hidden_child_parent_notice_does_not_schedule, tests/test_file_watcher.py::test_hidden_move_parent_notices_do_not_schedule, tests/test_file_watcher.py::test_real_changes_still_schedule_after_hidden_changes, tests/test_file_watcher.py::test_hidden_scratch_dir_in_nested_folder_is_ignored]
- Adding `FileClosedEvent` is unnecessary for CLOSE_WRITE and is harmful at the
  minimum: 4.0.2 maps it to `IN_CLOSE`, which also includes CLOSE_NOWRITE;
  7.1.0 maps it to CLOSE_WRITE alone. [source: ino4] [source: ino7]
  [pins: tests/test_file_watcher.py::test_start_filters_mutations_on_every_watch]
- FSEvents 4.0.0 and 4.0.1 override `schedule` without `event_filter`; 4.0.2
  accepts and forwards it, matching the changelog's fix #1049. Uniform keyword
  use therefore needs watchdog >=4.0.2. [source: fs400] [source: fs401]
  [source: fs402] [source: changes]
  Native macOS minimum-version execution is not verified here; the compatibility
  floor is source-verified, with Linux minimum-version smoke recorded separately.
- In 7.1.0, overflow recovery in recursive watch groups walks and re-registers
  descendants; eliminating read subscriptions removes a source of traffic from
  that walk, but does not bound traffic from real mutations. [source: ino7]
  [unverified] The reported large-vault CPU loop and any upper bound on overflow
  frequency have not been reproduced; a synthetic overflow experiment would be
  needed to quantify them.

- In inspected FSEvents 7.1, a combined modification and metadata flag is
  delivered as a modification; the metadata branch covers inode, extended
  attribute and owner changes. [source: fs7]

## Refutation pass

Comparing the three FSEvents overrides refutes the tempting >=4.0.0 floor;
4.0.1's filter fix concerned the inotify full emitter. Comparing the two inotify
mask implementations refutes both adding a ninth close class and dropping
CLOSE_WRITE. Following the native-mask path refutes a queue-only interpretation
on Linux. These findings do not establish native subscription reduction on other
platforms. [source: fs400] [source: fs401] [source: fs402] [source: changes]
[source: ino4] [source: ino7]

## Where this project departs from the subject

The project chooses eight mutation classes rather than watchdog's unfiltered
default. When observation is enabled, it also subscribes to attribute event
classes where watchdog provides them, routing these events to observation only.
Older versions deliver access changes as modification events. Each watch root
has a handler; they route search and observation through a shared debounce.
Observation-only trees split around internal directories; inherited search
roots retain their recursive subscriptions. On macOS with watchdog 7.1 and
Python 3.14, a non-recursive floor delivered root modification notices for
new, recreated and moved-in directories; a recursive watch did not survive
deletion and recreation of its root. A later nested `.git` creation was
delivered to its recursive observation watch. A probe of 400 non-recursive
registrations added 400 emitter threads; the source for the inspected inotify
emitter creates an instance per emitter. These probes do not establish native
Linux behavior, including native move-self delivery. [source: ino7] A replaced
search root is registered for observation only until restart. With the source
floor off, new, recreated or moved top-level roots rely on a later scan.
Non-recursive FSEvents registrations still use
recursive native streams, so the internal-directory boundary applies to
registrations. [source: fs402] The
search handler retains root-relative hidden-path checks; see
the [watcher design](../design.md). Observation does not suppress adjacent
parent notices: a directory notice can carry a coalesced access change. It can
cause one full sweep per quiet debounce window, including for non-note changes;
an unchanged sweep does not write the ledger. A direct directory event under a
non-recursive observation piece reconciles its children, so a child omitted
while unreadable can be watched after access is restored. Source inspection of
inotify shows that a directory created unreadable inside an already recursive
watch can be omitted: on watchdog 4.0.2 through 6.x a scan records its current notes,
but restoring watch coverage may require a restart. Search-owned roots retain their inherited behavior. FSEvents
probes with watchdog 4.0.2 and 7.1 delivered later edits after restoration through
its recursive stream; native Linux execution remains unverified. [source: ino4]
[source: ino7] The scheduling pin covers recursive visible
and dot-roots, the optional floor, excluded children and protected state paths.
[pins: tests/test_file_watcher.py::test_start_filters_mutations_on_every_watch]

## Not covered

Native Windows/BSD delivery, macOS minimum-version runtime, deep recursive
exclusions and overflow backoff are outside this reference's verified runtime
coverage. Existing native watcher tests cover normal delivery under the locked
dependency; they do not reproduce the reporter's 40,000-file workload.
