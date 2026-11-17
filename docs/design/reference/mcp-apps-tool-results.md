---
type: Reference
title: MCP Apps tool results
description: How ext-apps returns tool errors to the Vault Explorer.
subject_version: "@modelcontextprotocol/ext-apps 1.7.4"
valid_for: "ext-apps 1.7.4; re-check when the vendored SDK changes"
generated:
  by: process:researching-references
  at: 2026-11-16T18:50:00-08:00
stale_after: 2027-05-16T00:00:00+00:00
verified:
  - by: process:researching-references-refute
    at: 2026-11-16T18:50:00-08:00
status: stable
sources:
  - id: ext-apps-1-7-4
    title: MCP Apps ext-apps 1.7.4 app source (callServerTool and request)
    resource: https://unpkg.com/@modelcontextprotocol/ext-apps@1.7.4/dist/src/app.js
    accessed: 2026-11-16
---

# MCP Apps tool results

This reference covers the SDK result handling used by the Vault Explorer's
`static/spa/core.js` parser; it does not describe how hosts display errors.

## Claims

- In ext-apps 1.7.4, `callServerTool` returns the validated `tools/call` result from `request`; a result carrying `isError: true` resolves normally, while a JSON-RPC error can reject the request. [source: ext-apps-1-7-4] [pins: tests/test_vault_apps_accessibility.py::test_context_tool_error_shows_message_and_retry_succeeds]

## Where this project departs from the subject

The [design](../design.md) makes the parser throw the error result's text so
the view's existing error handling receives it.

## Not covered

Host-specific presentation of tool errors requires host-specific probes.
