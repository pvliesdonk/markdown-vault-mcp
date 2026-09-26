---
type: Reference
title: MCP tool outcomes and errors
description: How the MCP spec, FastMCP, the MCP Python SDK, pvl-core, model vendors and published servers separate a tool's negative outcome from a tool error, on the wire and in the logs.
subject_version: "MCP 2026-07-28 (with 2025-11-25 and 2025-06-18); FastMCP 4.0.9; mcp 2.2.0; fastmcp-pvl-core 10.0.0; vendor docs and published servers as of 2026-09-25"
valid_for: "MCP 2026-07-28 and FastMCP 4.x; vendor and server behaviour as of 2026-09"
generated:
  by: process:researching-references
  at: 2026-09-25T13:30:00+02:00
stale_after: 2027-03-25T00:00:00+00:00
verified:
  - by: process:researching-references-refute
    at: 2026-09-25T13:25:00+02:00
  - by: process:researching-references
    at: 2026-09-25T17:00:00+02:00
status: stable
sources:
  - id: mcp-schema
    title: MCP specification 2026-07-28, schema.ts (CallToolResult, ToolResultContent), commit ab3a39c1
    resource: https://github.com/modelcontextprotocol/modelcontextprotocol/blob/ab3a39c1/schema/2026-07-28/schema.ts
    accessed: 2026-09-25
  - id: mcp-schema-2025-11-25
    title: MCP specification 2025-11-25, schema.ts (TaskStatus)
    resource: https://github.com/modelcontextprotocol/modelcontextprotocol/blob/ab3a39c1/schema/2025-11-25/schema.ts
    accessed: 2026-09-25
  - id: mcp-tools
    title: MCP specification 2026-07-28, Tools, Error Handling and Output Schema
    resource: https://modelcontextprotocol.io/specification/2026-07-28/server/tools
    accessed: 2026-09-25
  - id: mcp-tools-2025-11-25
    title: MCP specification 2025-11-25, Tools, Error Handling
    resource: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
    accessed: 2026-09-25
  - id: mcp-tools-2025-06-18
    title: MCP specification 2025-06-18, Tools, Error Handling
    resource: https://modelcontextprotocol.io/specification/2025-06-18/server/tools
    accessed: 2026-09-25
  - id: mcp-resources
    title: MCP specification 2026-07-28, Resources, Error Handling
    resource: https://modelcontextprotocol.io/specification/2026-07-28/server/resources
    accessed: 2026-09-25
  - id: mcp-changelog-2025-11-25
    title: MCP specification 2025-11-25, changelog (SEP-1303)
    resource: https://modelcontextprotocol.io/specification/2025-11-25/changelog
    accessed: 2026-09-25
  - id: mcp-client-best-practices
    title: MCP docs 2026-07-28, client best practices, Error Handling (non-normative)
    resource: https://github.com/modelcontextprotocol/modelcontextprotocol/blob/ab3a39c1/docs/docs/2026-07-28/develop/clients/client-best-practices.mdx
    accessed: 2026-09-25
  - id: mcp-issue-199
    title: modelcontextprotocol issue 199, Guidance on what isError in tool result is
    resource: https://github.com/modelcontextprotocol/modelcontextprotocol/issues/199
    accessed: 2026-09-25
  - id: mcp-sep-1303
    title: modelcontextprotocol SEP-1303, input validation errors as tool execution errors
    resource: https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1303
    accessed: 2026-09-25
  - id: mcp-issue-569
    title: modelcontextprotocol issue 569, Unspecified error handling for tool calls with output schema
    resource: https://github.com/modelcontextprotocol/modelcontextprotocol/issues/569
    accessed: 2026-09-25
  - id: fastmcp-src
    title: FastMCP 4.0.9 source (exceptions.py, server/server.py call_tool, server/mixins/mcp_operations.py, tools/base.py, settings.py)
    resource: https://pypi.org/project/fastmcp/4.0.9/
    accessed: 2026-09-25
  - id: fastmcp-docs-tools
    title: FastMCP docs, Tools, Error Handling
    resource: https://gofastmcp.com/servers/tools#error-handling
    accessed: 2026-09-25
  - id: mcp-python-sdk
    title: MCP Python SDK 2.2.0 source (mcp/server/mcpserver/exceptions.py, server.py)
    resource: https://pypi.org/project/mcp/2.2.0/
    accessed: 2026-09-25
  - id: pvl-core-src
    title: fastmcp-pvl-core 10.0.0 source (_logging_middleware.py, _middleware.py, _tool_boundary.py)
    resource: https://github.com/pvliesdonk/fastmcp-pvl-core/tree/v10.0.0/src/fastmcp_pvl_core
    accessed: 2026-09-25
  - id: claude-handle-tool-calls
    title: Claude Developer Platform, Handle tool calls
    resource: https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls
    accessed: 2026-09-25
  - id: anthropic-writing-tools
    title: Anthropic engineering, Writing effective tools for agents, with agents
    resource: https://www.anthropic.com/engineering/writing-tools-for-agents
    accessed: 2026-09-25
  - id: openai-functions
    title: OpenAI, Function calling guide, Formatting results
    resource: https://developers.openai.com/api/docs/guides/function-calling
    accessed: 2026-09-25
  - id: openai-agents-tools
    title: OpenAI Agents SDK, Tools, Handling errors in function tools
    resource: https://openai.github.io/openai-agents-python/tools/
    accessed: 2026-09-25
  - id: gemini-interactions
    title: Google Gemini Interactions API reference, FunctionResultStep
    resource: https://ai.google.dev/api/interactions-api
    accessed: 2026-09-25
  - id: servers-filesystem
    title: modelcontextprotocol/servers filesystem server, commit f46d9578 (lib.ts, index.ts)
    resource: https://github.com/modelcontextprotocol/servers/tree/f46d9578/src/filesystem
    accessed: 2026-09-25
  - id: servers-memory-fetch
    title: modelcontextprotocol/servers memory and fetch servers, commit f46d9578
    resource: https://github.com/modelcontextprotocol/servers/tree/f46d9578/src
    accessed: 2026-09-25
  - id: ts-sdk
    title: MCP TypeScript SDK 1.30.1, McpServer tools/call handler (src/server/mcp.ts), commit 289ac2c3
    resource: https://github.com/modelcontextprotocol/typescript-sdk/blob/289ac2c3/src/server/mcp.ts
    accessed: 2026-09-25
  - id: github-mcp-server
    title: github/github-mcp-server, commit 85598ba6 (pkg/utils/result.go, pkg/errors/error.go, pkg/github)
    resource: https://github.com/github/github-mcp-server/tree/85598ba6
    accessed: 2026-09-25
  - id: sentry-mcp
    title: getsentry/sentry-mcp, commit a2544d8f (packages/mcp-core/src/server.ts, internal/error-handling.ts)
    resource: https://github.com/getsentry/sentry-mcp/tree/a2544d8f
    accessed: 2026-09-25
  - id: notion-mcp
    title: makenotion/notion-mcp-server, commit 730ae781 (src/openapi-mcp-server/mcp/proxy.ts)
    resource: https://github.com/makenotion/notion-mcp-server/tree/730ae781
    accessed: 2026-09-25
---

# MCP tool outcomes and errors

A tool call can end three ways that matter to a server author: it returns
what the caller asked for, it returns a negative answer (the note does not
exist, access is refused, nothing matched), or something went wrong while
producing the answer. This page records what the protocol, the frameworks,
the model vendors and published servers say and do about telling those
apart: on the wire (`isError`, JSON-RPC errors, normal results) and in the
server's logs. It records the world, not a decision. A project's own rule
for which outcomes set `isError` belongs in its design docs, with a link
back here. How older protocols, telemetry conventions and language error
models answer the same question is in the companion page,
[Negative outcomes and faults outside MCP](negative-outcomes-and-faults.md).

## Scope

- Covers: `CallToolResult.isError` and JSON-RPC errors for `tools/call`;
  what the spec and the vendors say the flag means; how FastMCP 4, the MCP
  Python SDK 2 and fastmcp-pvl-core turn raised exceptions and returned
  results into wire results and log records; what the reference servers and
  a sample of vendor servers return for not-found, access refused and empty
  results.
- Does not cover: resources and prompts beyond one contrast; task-augmented
  calls; elicitation; how any particular client renders an error result.
- Depended on by: the tool layer of every generated server (`tools.py` and
  anything it calls), `fastmcp-pvl-core`'s request-logging middleware and
  `tool_boundary`, and any project decision about which outcomes a tool
  reports as errors. `tests/test_tool_outcomes.py` fails a tool registered
  without `tool_boundary`, but no test asserts the level of any record this
  page describes, so no claim carries a pin.

## Claims

### What the protocol says

- A tool call has two error channels. **Protocol errors** are JSON-RPC
  `error` responses for "Unknown tool", "Malformed requests" and "Server
  errors". **Tool execution errors** are results with `isError: true` for
  "API failures", "Input validation errors" and "Business logic errors".
  [source: mcp-tools] (§Error Handling)
- The schema defines `isError` as "Whether the tool call ended in an error.
  If not set, this is assumed to be false (the call was successful)." It
  adds: "Any errors that originate from the tool SHOULD be reported inside
  the result object, with `isError` set to true, _not_ as an MCP
  protocol-level error response. Otherwise, the LLM would not be able to see
  that an error occurred and self-correct." The same text appears in every
  version from 2024-11-05 to 2026-07-28. [source: mcp-schema]
- Tool execution errors "contain actionable feedback that language models
  can use to self-correct and retry with adjusted parameters". Clients
  **SHOULD** give them to the model, and **MAY** give it protocol errors.
  [source: mcp-tools]
- In 2025-11-25, input validation errors moved from protocol errors to tool
  execution errors (SEP-1303). A sponsoring maintainer described the change
  as "mainly as clarifying the documentation, rather than being a strict
  change to the specification". 2025-06-18 still listed "Invalid arguments"
  under protocol errors. [source: mcp-changelog-2025-11-25]
  [source: mcp-sep-1303] [source: mcp-tools-2025-06-18]
- **The spec never says whether a valid negative outcome is an error.**
  "Business logic errors" is listed and never defined. No version's tools
  page or 2026-07-28 schema mentions not found, permission denied or an
  empty result for tools. The research searched for: not found, permission,
  denied, empty, no results, unsuccessful, negative, business logic.
  [source: mcp-tools] [source: mcp-tools-2025-11-25]
  [source: mcp-tools-2025-06-18] [source: mcp-schema]
- A core maintainer closed the request for guidance on `isError` as not
  planned: "It's purposely left up to the application to decide what to do
  in the face of a tool error vs. protocol error". [source: mcp-issue-199]
- By contrast, resources do have a not-found rule. `resources/read` of a
  missing resource is a JSON-RPC error `-32602`, and "Servers **MUST NOT**
  return an empty `contents` array for a non-existent resource. An empty
  array is ambiguous". Nothing equivalent exists for tools.
  [source: mcp-resources]
- The spec does not relax the output schema for error results: "If an
  output schema is provided: Servers **MUST** provide structured results
  that conform to this schema." Whether an `isError` result must carry
  conforming `structuredContent` is an open issue with no maintainer answer.
  [source: mcp-tools] (§Output Schema) [source: mcp-issue-569]
- In 2025-11-25, a tool call whose result has `isError: true` counts as a
  `failed` task ("For tool calls specifically, this includes cases where the
  tool call result has `isError` set to true"). Tasks later moved to an
  extension, which was not read. [source: mcp-schema-2025-11-25]
- The non-normative client best-practices page tells code-generating
  clients that "Generated wrappers should convert this [`isError: true`]
  into a thrown exception so model-authored code can use `try`/`catch`".
  In such a client an `isError` result becomes an exception in the model's
  code. [source: mcp-client-best-practices]

### What model vendors say the flag means

- Anthropic: `is_error` is "Set to `true` if the tool execution resulted in
  an error." The worked example is "a network error when fetching weather
  data", and Claude then "will incorporate this error into its response to
  the user". The same page advises: "Write instructive error messages …
  include what went wrong and what Claude should try next".
  [source: claude-handle-tool-calls]
- Anthropic's tool-writing guidance speaks of error *responses* ("clearly
  communicate specific and actionable improvements, rather than opaque error
  codes or tracebacks") without saying which outcomes are errors, and never
  mentions `is_error`. [source: anthropic-writing-tools]
- OpenAI's function-calling API has no error flag. The output is a string
  "where the format is up to you (JSON, error codes, plain text, etc.)".
  The Agents SDK's error path is for when "the tool call crashes".
  [source: openai-functions] [source: openai-agents-tools]
- Gemini's Interactions API has `is_error`: "Whether the tool call resulted
  in an error." [source: gemini-interactions]
- No vendor document read says whether "not found", "permission denied" or
  "no results" should set the flag. Where one defines it, the definition is
  about execution failing. [source: claude-handle-tool-calls]
  [source: openai-agents-tools] [source: gemini-interactions]
- Claude Code's and VS Code's MCP documentation do not describe how they
  handle an `isError` result. [unverified] Only a host-side probe would
  settle it.

### How FastMCP 4.0.9 maps a tool's behaviour to the wire and the logs

- `FastMCPError(*args, log_level=logging.ERROR)` is the base of
  `ToolError`, `ResourceError`, `PromptError` and `ValidationError`.
  `log_level` is read only by the server's log call. `ToolError`'s docstring
  is "Error in tool operations." [source: fastmcp-src] (`exceptions.py`)
- `call_tool` handles a tool's exception in four branches:
  - FastMCP's `ValidationError` is logged as a warning.
  - A `FastMCPError` (including `ToolError`) is logged at `e.log_level` with
    `exc_info=False` and message `Error calling tool 'x'`, without the
    exception's text.
  - A pydantic `ValidationError` is logged as a warning.
  - Any other exception goes to `logger.exception(...)` (ERROR, with
    traceback) and is wrapped in a `ToolError`. When `mask_error_details` is
    on, the wrapper hides the original message.

  The logger is `fastmcp.server.server`. [source: fastmcp-src]
  (`server/server.py`)
- That logging runs in the innermost dispatch, inside the middleware chain.
  Middleware sees the exception only after the record is written, and
  cannot suppress it except by answering the call without `call_next`.
  [source: fastmcp-src] (`server/server.py`, `run_middleware=False` path)
- A raised `FastMCPError` becomes `CallToolResult(content=[TextContent(str(e))],
  isError=True)` with no structured content. The code comment says:
  "Tool-visible errors … must be RETURNED as an error result, never raised".
  [source: fastmcp-src] (`server/mixins/mcp_operations.py`)
- A tool can report an error without raising: `ToolResult(is_error=True)`
  "maps to CallToolResult.is_error so the error is returned to the client
  rather than raised". A returned `CallToolResult` passes through unchanged.
  [source: fastmcp-src] (`tools/base.py`)
- The server does not validate structured output against `outputSchema`,
  for error or normal results. The FastMCP client skips structured parsing
  when `isError` is true. [source: fastmcp-src]
- `mask_error_details` (env `FASTMCP_MASK_ERROR_DETAILS`, default off)
  masks only non-`FastMCPError` exceptions. "Error messages from ToolError
  are always sent to clients, regardless of mask_error_details setting".
  [source: fastmcp-docs-tools] [source: fastmcp-src] (`settings.py`)
- The four ways a tool can end, as they reach the wire and the logs
  [observed: in-memory FastMCP 4.0.9 server with pvl-core 10.0.0's
  `RequestLoggingMiddleware`, called through `fastmcp.Client.call_tool_mcp`,
  log records captured on the `fastmcp` and `fastmcp_pvl_core` loggers,
  2026-09-25; the first five rows were first observed on pvl-core 9.0.1,
  where the middleware column read ERROR for every raise]:

  | The tool | `isError` | `fastmcp.server.server` | pvl-core `fastmcp.middleware.requests` |
  |---|---|---|---|
  | raises `ToolError(msg)` | true, `msg` | ERROR, no traceback | ERROR `tool_call_failed` |
  | raises `ToolError(msg, log_level=INFO)` | true, `msg` | INFO, no traceback | INFO `tool_call_failed` |
  | raises `ValueError(msg)` | true, `Error calling tool 'x': msg` (masked: without `msg`) | ERROR with traceback | ERROR `tool_call_failed` |
  | returns `ToolResult(..., is_error=True)` | true | nothing | INFO `tool_call_completed` |
  | returns a normal value such as `{"found": false}` | false | nothing | INFO `tool_call_completed` |
  | raises `ValueError(msg)` under pvl-core's `tool_boundary` | true, the boundary's fixed "the request itself was fine" message | ERROR, no traceback | ERROR `tool_call_failed`, after the boundary's own ERROR `tool_failed` with the traceback |

- FastMCP's documentation describes non-`ToolError` exceptions as
  "converted into an MCP error response". The code produces an `isError`
  *result*, not a JSON-RPC error. The documentation does not mention
  `log_level`. [source: fastmcp-docs-tools] [source: fastmcp-src]

### How the MCP Python SDK 2.2.0 separates the same cases

- The SDK's `ToolError` is "A tool failure you anticipated … the call
  returns `is_error=True` with your message in `content` for the model to
  read, and the server logs it at INFO without a traceback. … Any other
  exception bar `MCPError` (a protocol error) is treated as a crash: the
  model sees only `Error executing tool <name>`, and the server logs the
  traceback at ERROR." [source: mcp-python-sdk] (`mcpserver/exceptions.py`)
- The SDK wraps a crash in `UnexpectedToolError` (a `ToolError` subclass).
  Its docstring says to catch it "to tell a crash from a deliberate
  `ToolError`". [source: mcp-python-sdk] (`mcpserver/exceptions.py`)
- So FastMCP and the SDK agree on the wire: both an anticipated failure and
  a crash become `isError: true`. They disagree on the logs: the SDK logs an
  anticipated `ToolError` at INFO, while FastMCP defaults it to ERROR unless
  the tool passes `log_level`. [source: mcp-python-sdk] [source: fastmcp-src]

### What fastmcp-pvl-core's middleware and boundary record

- `wire_middleware_stack` installs only `RequestLoggingMiddleware`
  (`include_traceback` follows a DEBUG root logger). It wires no
  error-handling middleware. [source: pvl-core-src] (`_middleware.py`)
- `RequestLoggingMiddleware.on_message` logs `tool_call_failed` for any
  exception leaving `call_next`, with fields `error_type` and `error`: at
  the exception's `log_level` when it is a `FastMCPError`, at ERROR
  otherwise. 9.0.1 and earlier logged it at ERROR regardless. A normal
  return logs `tool_call_completed` at INFO, and the middleware never
  inspects `is_error` on the result. [source: pvl-core-src]
  (`_logging_middleware.py`) [observed: same probe as the FastMCP table]
- `tool_boundary` passes a `FastMCPError`, and an `MCPError` with code
  -32021 (missing client capability), through unchanged. An upstream 429
  or timeout becomes a `ToolError` at WARNING, logged as `tool_failed`
  without a traceback. Any other exception is logged once as `tool_failed`
  at ERROR with the traceback and replaced, `from None`, by a `ToolError`
  with a fixed message saying the request was fine. `is_tool_boundary`
  follows `__wrapped__` to find the wrapper. [source: pvl-core-src]
  (`_tool_boundary.py`) The last case is also [observed: same probe as the
  FastMCP table]; the 429, timeout and -32021 branches are read from source
  only.

### What published servers return

- **Empty searches are normal results everywhere read:**
  - the filesystem server's `search_files` returns the text
    "No matches found";
  - GitHub's repository search returns `total_count: 0`;
  - Sentry returns "No issues found matching your search criteria …".

  [source: servers-filesystem] [source: github-mcp-server] [source: sentry-mcp]
- **Access outside the allowed scope is an `isError` result.** The
  filesystem server throws "Access denied - path outside allowed
  directories", and the TypeScript SDK wraps every thrown exception as
  `isError: true`. [source: servers-filesystem] [source: ts-sdk]
- **A missing single item is mostly an `isError` result:**
  - the filesystem server's raw ENOENT;
  - the memory server's "Entity with name … not found";
  - GitHub's `get_file_contents` when no candidate path is found;
  - Sentry's HTTP 404/403 responses, which it formats as "Input Error".

  [source: servers-filesystem] [source: servers-memory-fetch]
  [source: github-mcp-server] [source: sentry-mcp]
- **Partial or batch misses are reported as data:**
  - the memory server's `deleteEntities` returns `{deleted, notFound}`;
  - `delete_observations` returns `success: true` with a count of entities
    not found;
  - `read_multiple_files` puts per-file errors into a normal result;
  - GitHub's `get_file_contents` returns "potential matches" as a success
    when the tree search finds candidates.

  [source: servers-memory-fetch] [source: servers-filesystem]
  [source: github-mcp-server]
- Notion's server returns an upstream HTTP 4xx (including
  `object_not_found`) as a normal result whose JSON body has
  `status: "error"`, with no `isError`. The line carries a
  `// TODO: get this from http status code?` comment. [source: notion-mcp]
- Sentry marks its 4xx responses (user-input errors, API client errors) as
  "Expected/user-facing tool failures that should return a formatted MCP
  error response without creating a Sentry issue". It reports them with
  `isError: true` on the wire, and keeps them out of fault telemetry
  internally. [source: sentry-mcp] (`internal/error-handling.ts`)
- The fetch server raises `McpError(INTERNAL_ERROR)` for HTTP 4xx and
  robots.txt refusals. Its authors evidently meant these as protocol errors.
  The Python SDK v1 it pins turns any exception from `call_tool` into
  `isError: true`, so on the wire they are error results. [source:
  servers-memory-fetch] [unverified] Whether v1.29.0 still does this for
  `McpError` was read from source by the research pass and not re-probed.
- No server read had a stale-version or precondition-failed outcome to
  compare. [unverified] A server with optimistic concurrency would settle
  it.

## Where the sources leave the choice open

- **On the wire**, nothing normative separates a negative outcome from an
  error. The spec leaves "business logic errors" undefined, and practice is
  mixed: empty results are data, scope refusals are errors, single-item
  not-found leans towards error, batch misses are data. A server choosing
  either way for "not found" or "permission denied" contradicts no spec
  text. The choice has consequences the sources do record:
  - an `isError` result is a failed task in 2025-11-25;
  - it is an exception in code-mode clients;
  - Anthropic's example has Claude report it to the user.
- **In the logs**, the frameworks do separate "anticipated" from "crash":
  - the Python SDK logs `ToolError` at INFO and crashes at ERROR with a
    traceback;
  - FastMCP separates them by traceback only, since both default to ERROR;
  - pvl-core's middleware separates them by the `ToolError`'s
    `log_level` since 10.0.0, and `tool_boundary` turns a crash into one
    ERROR `tool_failed` record before FastMCP sees it;
  - Sentry separates expected 4xx from faults in its own telemetry.

  A tool that returns an `isError` result instead of raising is logged by
  both FastMCP and pvl-core as a completed call.

## Not covered

- How Claude Code, claude.ai, VS Code and Cursor display or act on an
  `isError` result, and whether any retries on it; only host probes would
  settle it.
- The tasks extension's treatment of `isError` after tasks left the core
  schema in 2026-07-28.
- SEP-2643 (structured authorization denials, draft) and SEP-2140/2145
  (unknown tools as execution errors, open); neither is part of any spec
  version.
- Resources and prompts, beyond the resource not-found contrast above:
  FastMCP reports their errors as JSON-RPC errors because they have no
  error-result shape.
