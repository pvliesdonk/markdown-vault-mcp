---
type: Reference
title: FastMCP 4 protocol and server behaviour
description: FastMCP 4's SDK field names, protocol-era elicitation, templated-resource screening, per-server task backend, and OAuth route mounting as used by this server
subject_version: "FastMCP 4.0.3 (7129236c); fastmcp-pvl-core 7.1.0 (f2b264439)"
valid_for: "FastMCP 4.x and fastmcp-pvl-core 7.x; re-research on either next major"
generated:
  by: process:researching-references
  at: 2026-09-13T08:36:55+02:00
verified:
  - by: process:researching-references-refute
    at: 2026-09-13T08:40:43+02:00
stale_after: 2027-03-13
status: stable
sources:
  - id: upgrade
    title: FastMCP, Upgrading from FastMCP 3, at commit 7129236c
    resource: https://github.com/jlowin/fastmcp/blob/7129236c770e14d52e6bd14af1100f7c9450769c/docs/getting-started/upgrading/from-fastmcp-3.mdx
    accessed: 2026-09-13
  - id: elicitation
    title: FastMCP, Elicitation, at commit 7129236c
    resource: https://github.com/jlowin/fastmcp/blob/7129236c770e14d52e6bd14af1100f7c9450769c/docs/servers/elicitation.mdx
    accessed: 2026-09-13
  - id: resources
    title: FastMCP, Resources and Templates, at commit 7129236c
    resource: https://github.com/jlowin/fastmcp/blob/7129236c770e14d52e6bd14af1100f7c9450769c/docs/servers/resources.mdx
    accessed: 2026-09-13
  - id: resource-security
    title: FastMCP ResourceSecurity implementation at commit 7129236c
    resource: https://github.com/jlowin/fastmcp/blob/7129236c770e14d52e6bd14af1100f7c9450769c/fastmcp_slim/fastmcp/resources/security.py
    accessed: 2026-09-13
  - id: apps-architecture
    title: FastMCP, Apps Architecture, at commit 7129236c
    resource: https://github.com/jlowin/fastmcp/blob/7129236c770e14d52e6bd14af1100f7c9450769c/docs/apps/architecture.mdx
    accessed: 2026-09-13
  - id: app-addressing
    title: FastMCP app-tool addressing implementation at commit 7129236c
    resource: https://github.com/jlowin/fastmcp/blob/7129236c770e14d52e6bd14af1100f7c9450769c/fastmcp_slim/fastmcp/server/providers/addressing.py
    accessed: 2026-09-13
  - id: http
    title: FastMCP HTTP application route assembly at commit 7129236c
    resource: https://github.com/jlowin/fastmcp/blob/7129236c770e14d52e6bd14af1100f7c9450769c/fastmcp_slim/fastmcp/server/http.py
    accessed: 2026-09-13
  - id: auth
    title: FastMCP OAuth provider routes at commit 7129236c
    resource: https://github.com/jlowin/fastmcp/blob/7129236c770e14d52e6bd14af1100f7c9450769c/fastmcp_slim/fastmcp/server/auth/auth.py
    accessed: 2026-09-13
  - id: pvl-tasks
    title: fastmcp-pvl-core task-backend assembly at commit f2b264439
    resource: https://github.com/pvliesdonk/fastmcp-pvl-core/blob/f2b2644395b55368ca339b07e75d3b68d2c66883/src/fastmcp_pvl_core/_tasks.py
    accessed: 2026-09-13
---

# FastMCP 4 protocol and server behaviour

This page records the FastMCP 4 and fastmcp-pvl-core 7.1 behaviour on which
the server's #1271 migration depends. It covers Python SDK names, the two
elicitation eras, resource-template input screening, task-extension ownership,
and the OAuth route detail already documented for prefixed deployments. It
does not attempt to catalogue the rest of FastMCP's API.

## Scope

- Covers: SDK field naming; default client protocol negotiation; legacy and
  modern elicitation; resource-template path screening; task-backend
  registration; OAuth authorization-server metadata mounting.
- Does not cover: FastMCP middleware, transforms, client authentication, or
  task execution semantics beyond backend registration.
- Depended on by: `src/markdown_vault_mcp/server.py`,
  `_tools/writer.py`, `resources.py`, and `_server_apps.py`;
  the server section of `docs/design/design.md`; and the operator guidance in
  `docs/guides/okf.md` and `docs/deployment/oidc.md`.

## Claims

### SDK v2 names and protocol negotiation

- FastMCP 4 uses snake_case Python attributes for MCP SDK models while their
  JSON wire aliases remain camelCase; FastMCP's camelCase read bridge warns
  and is scheduled for removal. [source: upgrade]
  [pins: tests/test_client_surface_budget.py::test_maximal_client_surface_stays_within_reviewed_budgets]
- A default `fastmcp.Client` uses `mode="auto"` and negotiates the modern
  MCP 2026-07-28 era when the server supports it; `mode="legacy"` selects a
  handshake-era connection. [source: upgrade]
  [pins: tests/test_okf_write.py::TestOkfVerifyElicit::test_legacy_connection_uses_server_initiated_elicitation]

### Elicitation

- On a modern MCP 2026-07-28 connection, a handler cannot call
  `ctx.elicit()` mid-request. It returns an `InputRequiredResult`, and the
  client answers by making a fresh request whose keyed answers are available
  through `ctx.input_responses`. [source: elicitation]
  [pins: tests/test_okf_write.py::TestOkfVerifyElicit::test_writes_on_affirmative_elicitation]
- On handshake-era connections, the inverse applies: the handler calls
  `ctx.elicit()`, and returning `InputRequiredResult` is rejected. A server
  supporting both eras must branch on `ctx.request_context.protocol_version`.
  [source: elicitation]
  [pins: tests/test_okf_write.py::TestOkfVerifyElicit::test_legacy_connection_uses_server_initiated_elicitation]
- A modern elicitation answer must be checked for `action == "accept"` before
  reading `content`; decline and cancel answers may carry no content.
  [source: elicitation]
  [pins: tests/test_okf_write.py::TestOkfVerifyElicit::test_declined_elicitation_writes_nothing, tests/test_okf_write.py::TestOkfVerifyElicit::test_negative_reply_writes_nothing]

### Resource-template path screening

- FastMCP 4 screens every non-exempt templated-resource parameter after URI
  matching and percent-decoding but before the handler runs, rejecting
  standalone `..` path components, absolute paths, and null bytes by default.
  [source: resources] [source: resource-security]
  [pins: tests/test_server.py::TestResources::test_toc_resource_folder_traversal_raises]
- Dots inside an ordinary segment are not traversal, and separators in a
  percent-encoded relative value remain usable when the decoded value has no
  forbidden component. [source: resource-security]
  [pins: tests/test_server.py::TestResources::test_toc_resource_encoded_nested_note]
- Screening is not containment validation: handlers that map a value to the
  filesystem must still resolve it under their allowed root. [source: resources]

### Per-server task backend

- pvl-core 7.1's `configure_task_backend(mcp, env_prefix, config)` constructs
  and registers a `TasksExtension` on the specific FastMCP server before it
  starts; a mounted child's extension does not configure the root server.
  [source: pvl-tasks]
  [pins: tests/test_task_backend.py::test_make_server_configures_the_task_backend]
- An explicit project `TASKS_URL` wins; otherwise a Redis `KV_STORE_URL` is
  reused, and otherwise the extension's native setting or `memory://` default
  applies. [source: pvl-tasks]
  [pins: tests/test_task_backend.py::test_tasks_url_reaches_the_backend, tests/test_task_backend.py::test_redis_kv_store_url_is_reused_for_tasks]
- FastMCP 4's `Client.call_tool()` transparently polls a protocol-native task
  to completion, while `mode="legacy"` cannot negotiate tasks and exercises a
  task-capable tool's foreground fallback. [source: upgrade]
  [pins: tests/test_server.py::TestReindexTool::test_reindex_slow_run_promotes_to_job, tests/test_summarize_tool.py::TestSummarizeDualMode::test_slow_summary_promotes_then_completes]

### MCP Apps backend addressing

- An app-only backend tool is callable through
  `<hash>_<registered-tool-name>`, where the deterministic hash derives from
  the app name and registered tool name; the provider resolves this identity
  without exposing the tool to the model. [source: apps-architecture]
  [source: app-addressing]
  [pins: tests/test_mcp_apps_browser.py::TestBrowserDataTools::test_vault_list_root]
- FastMCP 4 stores that identity under the public
  `meta["fastmcp"]["tool_hash"]` key. An underscore-prefixed key is private
  metadata and is stripped at serialization boundaries, so `_tool_hash` does
  not participate in the hashed lookup. [source: app-addressing]
  [pins: tests/test_mcp_apps_foundation.py::TestSPARewriteValidation::test_app_tool_meta_uses_public_hash_key]
- App-only backend tools still appear in the protocol `tools/list` result with
  `meta.ui.visibility == ["app"]`; the host filters that declaration out of
  the model-visible catalog. [source: apps-architecture]
  [pins: tests/test_mcp_apps_context.py::TestShowContextTool::test_show_context_visible_to_llm]

### OAuth route mounting

- FastMCP's HTTP app mounts `auth.get_routes(mcp_path=...)`, while an OAuth
  provider's path-aware RFC 8414 authorization-server aliases are produced by
  its separate `get_well_known_routes()` helper. Consequently the full route
  set still publishes authorization-server metadata at the host-root path in
  FastMCP 4.0.3. [source: http] [source: auth]

## Where this project departs from the subject

- There is no resource-security exemption. Vault resource parameters are
  always vault-relative paths, so an absolute path or a standalone `..`
  component is invalid input. The Vault reader remains the containment layer;
  FastMCP screening is an additional early refusal. This is decided in
  `docs/design/design.md` under `server.py`: Generic MCP Server.
- `okf_verify` supports both protocol eras instead of requiring operators to
  pin their clients to one. Its modern path returns one boolean input request;
  its legacy path retains `ctx.elicit(response_type=bool)`.

## Not covered

- FastMCP's full Apps composition and late-bound name-rewrite protocol are
  outside this server's direct low-level SPA integration.
