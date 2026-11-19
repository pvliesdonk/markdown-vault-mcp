---
type: Reference
title: Linux ownership of disposable PostgreSQL test containers
kind: reference
description: Namespace identity, process liveness and static Docker labels used to reclaim this harness's abandoned containers.
subject_version: "Linux 6.8 and nsfs UAPI at 23eb7901811a06805ec7d6e766dd82f083d03204; Linux man-pages 6.19; Docker labels documentation read 2026-11-14"
valid_for: "Linux with nsfs NS_GET_NSTYPE and Docker container labels"
generated:
  by: process:researching-references
  at: 2026-11-14T12:00:00+00:00
stale_after: 2027-05-14T12:00:00+00:00
status: stable
sources:
  - id: linux-initial
    title: Linux 6.8 initial namespace inode constants
    resource: https://raw.githubusercontent.com/torvalds/linux/v6.8/include/linux/proc_ns.h
    accessed: 2026-11-14
  - id: linux-nsfs
    title: Linux nsfs UAPI at 23eb7901811a06805ec7d6e766dd82f083d03204
    resource: https://raw.githubusercontent.com/torvalds/linux/23eb7901811a06805ec7d6e766dd82f083d03204/include/uapi/linux/nsfs.h
    accessed: 2026-11-14
  - id: linux-pid
    title: Linux PID namespaces
    resource: https://man7.org/linux/man-pages/man7/pid_namespaces.7.html
    accessed: 2026-11-14
  - id: linux-namespace
    title: Linux namespace handles and identity
    resource: https://man7.org/linux/man-pages/man7/namespaces.7.html
    accessed: 2026-11-14
  - id: linux-kill
    title: Linux kill(2)
    resource: https://man7.org/linux/man-pages/man2/kill.2.html
    accessed: 2026-11-14
  - id: docker-labels
    title: Docker object labels
    resource: https://docs.docker.com/engine/manage-resources/labels/
    accessed: 2026-11-14
---

# Linux ownership of disposable PostgreSQL test containers

## Scope

The consumer is `tests/fixtures/postgres.py`, not production storage.
This page supports the bounded recovery policy in the
[testing strategy](../design.md#testing-strategy), introduced for #1952.

## Claims

- Linux fixes the initial PID namespace inode at `0xEFFFFFFC` and the
  initial user namespace inode at `0xEFFFFFFD`; the newer nsfs UAPI exposes
  these values as `PID_NS_INIT_INO` and `USER_NS_INIT_INO`.
  [source: linux-initial] [source: linux-nsfs]
  [pins: tests/test_postgres_cleanup.py::test_initial_namespace_requires_genuine_initial_handle]
- `NS_GET_NSTYPE` on a namespace handle returns its `CLONE_NEW*` type;
  namespace device/inode pairs identify the namespace. The harness checks
  the handle type before accepting the initial inode.
  [source: linux-nsfs] [source: linux-namespace]
  [pins: tests/test_postgres_cleanup.py::test_initial_namespace_requires_genuine_initial_handle]
- PID syscalls use the caller's PID namespace, but a procfs mount displays
  the namespace of its mounting process; `/proc/self` resolves the caller
  within that mount. Thus ownership reads use `/proc/self/ns/pid`, never
  `/proc/<getpid()>/ns/pid` or `pid_for_children`.
  [source: linux-pid] [source: linux-namespace]
  [pins: tests/test_postgres_cleanup.py::test_initial_namespace_requires_genuine_initial_handle]
- Signal zero performs existence and permission checks without delivering
  a signal; `ESRCH` means the target does not exist, while `EPERM` means
  permission was denied. A successful check does not distinguish a reused
  PID from the original process, so the harness preserves either.
  [source: linux-kill]
  [pins: tests/test_postgres_cleanup.py::test_only_esrch_reclaims_exact_id,
  tests/test_postgres_cleanup.py::test_real_process_liveness_is_conservative]
- Docker container labels remain static for the object's lifetime, and
  `docker run --label` can override inherited image labels. The harness
  sets every ownership field on creation; unsupported scopes explicitly
  override the version with `unsupported` to prevent inherited metadata
  from making a new container collectable.
  [source: docker-labels]
  [pins: tests/test_postgres_cleanup.py::test_start_labels_current_creator_before_docker_run]

## Project policy and limits

Recovery is deliberately narrower than Linux's namespace APIs: only initial
PID and user namespaces, equal real/effective UID, and a matching version,
boot UUID, hostname, namespace device/inode pairs and UID qualify. Namespace
read failures disable recovery. Unknown, incomplete, foreign and legacy
metadata stays untouched. Only `ESRCH` for a strictly positive creator PID
permits removal, by the inspected full container ID, never by name.

The sweep has a shared ten-second budget, including discovery, inspection
and removal. It runs before `start_container` creates its next container;
explicit external-DSN runs never enter this path. Ordinary teardown and
startup failure logs are unchanged. A SIGKILL is recovered on a subsequent
supported start, not immediately. Old boots, nested/rootless test runners,
macOS and ambiguous PID reuse may leave containers for manual cleanup.

Labels are ownership evidence within the trusted test harness, not protection
against an actor who can forge labels or administer Docker. Arbitrary modified
kernels, forged procfs mounts and syscall emulation are outside this contract.

## Runtime validation

`test_sigkill_orphan_reclaimed_without_touching_other_containers` is the real
Docker regression selected by the existing PostgreSQL-range workflow. It uses
a unique test-only label namespace, recorded disposable IDs and its own
subprocesses, so its sweep cannot discover other runs on a shared daemon.
Missing supported identity fails under `CI=true`. The development container has no Docker daemon,
so local mocked tests do not establish that the real regression passes.
Cross-namespace Docker-socket sharing is not exercised by this regression;
namespace rejection has unit coverage and a read-only check of the developer
container's existing nested namespaces, without creating privileged namespaces.
