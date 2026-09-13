---
type: Reference
title: fastmcp-pvl-core transfer errors and retries
description: Transfer sink HTTP error mapping, failure release, and successful-transfer replay in fastmcp-pvl-core
subject_version: "fastmcp-pvl-core 7.1.0 (f2b2644395b55368ca339b07e75d3b68d2c66883)"
valid_for: "fastmcp-pvl-core 7.1.0; re-check when the transfer subsystem changes"
generated:
  by: process:researching-references
  at: 2026-09-13T18:35:45+02:00
verified:
  - by: process:researching-references-refute
    at: 2026-09-13T18:35:45+02:00
stale_after: 2027-03-13
status: stable
sources:
  - id: sink
    title: TransferSinkError and domain sink protocol at v7.1.0
    resource: https://github.com/pvliesdonk/fastmcp-pvl-core/blob/f2b2644395b55368ca339b07e75d3b68d2c66883/src/fastmcp_pvl_core/_transfer/sink.py
    accessed: 2026-09-13
  - id: routes
    title: Transfer HTTP handlers at v7.1.0
    resource: https://github.com/pvliesdonk/fastmcp-pvl-core/blob/f2b2644395b55368ca339b07e75d3b68d2c66883/src/fastmcp_pvl_core/_transfer/routes.py
    accessed: 2026-09-13
  - id: store
    title: Transfer token claim, release, and completion at v7.1.0
    resource: https://github.com/pvliesdonk/fastmcp-pvl-core/blob/f2b2644395b55368ca339b07e75d3b68d2c66883/src/fastmcp_pvl_core/_transfer/store.py
    accessed: 2026-09-13
  - id: error-history
    title: Original missing sink error mapping report, fastmcp-pvl-core issue 233
    resource: https://github.com/pvliesdonk/fastmcp-pvl-core/issues/233
    accessed: 2026-09-13
---

# fastmcp-pvl-core transfer errors and retries

## Scope

- Covers: the error contract consumed by `VaultTransferSink`, and why a
  released or successfully used upload link can invoke the sink again.
- Depended on by: `src/markdown_vault_mcp/_transfer_sink.py` and the
  [transfer domain seam](../design.md#domain-seam-vaulttransfersink).
- Does not cover: transfer authentication, body limits, shared-store
  deployment, or the rest of the core API.

The installed 7.1.0 `sink.py`, `routes.py`, and `store.py` were compared
byte-for-byte with the pinned upstream commit through GitHub's contents API.
The refute pass checked the exception branches and store transitions against
their docstrings, including the failure and expiry caveats below.

## Claims

### Domain errors

- A sink signals an expected HTTP error with `TransferSinkError`; the base
  accepts a status and optional message, so `TransferSinkError(409, message)`
  needs no new core subclass. [source: sink]
  [pins: tests/test_transfer_routes.py::test_upload_conflict_returns_409]
- The named `TransferResourceGoneError` and `TransferUnavailableError`
  subclasses carry 410 and 503 respectively. [source: sink]
  [pins: tests/test_transfer_sink.py::test_read_missing_note_raises_gone, tests/test_transfer_sink.py::test_write_vault_unavailable_raises_503]
- The upload handler catches `TransferSinkError`, attempts to release the
  claim, and returns the selected status with an empty response body; it
  does not send the exception message to the client. [source: routes]
  [pins: tests/test_transfer_routes.py::test_upload_conflict_returns_409]
- An ordinary sink exception outside that hierarchy follows release and
  re-raise, reaching the surrounding HTTP framework as a generic 500;
  the handler does not know the vault's `DocumentExistsError` type.
  [source: routes] [source: sink]

The original report concerned core 4.4.0, before sink-selected status codes
were available; its statement that all sink errors become 500 must not be
applied to 7.1.0's `TransferSinkError` branch. [source: error-history]
[source: routes]

### Claim lifetime and replay

- `release` returns the current reservation to available without extending
  its remaining TTL; it does nothing for an expired token or a superseded
  reservation. A failed upload can therefore retry while its token remains
  valid. [source: store]
  [pins: tests/test_transfer_routes.py::test_upload_conflict_returns_409]
- `_release_quietly` logs and suppresses an ordinary backend failure during
  release, preserving the transfer's original error; immediate retryability
  is consequently conditional on successful release, not guaranteed during
  a store outage. [source: routes]
- A successful upload calls `complete`, which returns the reservation to
  available with TTL `min(remaining, grace_seconds)`; another claim during
  that window invokes `sink.write` again, with no cached upload result.
  [source: routes] [source: store]
  [pins: tests/test_transfer_routes.py::test_upload_conflict_returns_409]

## Where this project departs from the subject

Core permits replay but does not promise that a domain write accepts it.
The vault's default overwrite protection rejects a destination created after
minting, including by a previous successful upload, with HTTP 409 and leaves
the existing bytes intact. Releasing the token does not remove that conflict.
The [domain seam](../design.md#domain-seam-vaulttransfersink) defines this
policy; core's token state machine remains unchanged.
[pins: tests/test_transfer_routes.py::test_upload_conflict_returns_409]

## Not covered

Backend-outage and expiry timing behaviour is sourced from core rather than
reproduced in the vault's tests. The route pin checks immediate retry after
removing an upload conflict, including a replay after success; it does not
assert a configured grace duration or distributed-store consistency.
