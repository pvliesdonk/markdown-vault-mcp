# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed (BREAKING)

- `IndexBuildFailedError` removed from the public exception surface
  (#533). Captured background-build errors are now diagnostic events
  surfaced via `Collection.get_index_status` instead of raised from
  the read path. The MCP `@needs_index_ready` decorator catches
  `sqlite3.OperationalError` from handlers and remaps to
  `IndexNotReadyError(reason="broken")` — the new structured
  `reason` discriminator on `IndexNotReadyError` distinguishes
  `"never_built"`, `"timeout"`, and `"broken"`. External consumers
  that previously caught `IndexBuildFailedError` should remove that
  catch arm.
- `Collection.get_index_status` renames `status="ready"` to
  `status="queryable"` and flips the priority so a previously-built
  index with a captured rebuild error reports `"queryable"` (with the
  diagnostic in `error`) rather than `"failed"`. `"failed"` now means
  not queryable AND a build attempt errored. Issue #534 will further
  subdivide `"queryable"` into `"up_to_date"` / `"degraded"` once
  drift detection lands.
