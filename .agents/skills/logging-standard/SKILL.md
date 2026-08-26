---
name: logging-standard
description: >-
  Use before adding or changing any logging call in src/: the structlog-based standard, log levels, exception handling, and message format every module follows.
---

<!-- ===== TEMPLATE-OWNED — re-rendered on template updates. ===== -->

# Logging Standard

## Logging Standard

### Scope

This standard governs **first-party code only** — `src/markdown_vault_mcp/` and
`tests/`. Two categories of log output are explicitly **out of scope**; do not
try to make them conform:

- **FastMCP middleware stack** (`fastmcp-pvl-core` timing / logging /
  error-handling middleware): emits conforming, bare-event-name-first lines
  automatically. Tool-call lines carry `tool=<name>`. Governed by
  `fastmcp-pvl-core` itself (see pvliesdonk/fastmcp-pvl-core#90); no
  first-party code needed.
- **`uvicorn.access` and `mcp.server.lowlevel.server` log lines**: upstream
  transport / SDK output. `configure_logging_from_env` raises their effective
  level to `WARNING`, suppressing per-request `INFO` output at the default
  level; at `DEBUG` they are reset to `NOTSET` so the root logger governs
  their effective level via propagation (pvliesdonk/fastmcp-pvl-core#91).
  `uvicorn.error` is intentionally not suppressed — it carries startup and
  bind failures. Do not silence or reformat any of these — they are out of
  scope by design.

### Framework
- Standard library `logging` throughout. Every module: `logger = logging.getLogger(__name__)`.
- No `print()` for operational output. No third-party logging libraries.
- FastMCP middleware handles tool invocation, timing, and error logging automatically.
- All first-party logging goes through FastMCP's `configure_logging_from_env()` for uniform output. `FASTMCP_LOG_LEVEL` is the single log level control; the `-v` CLI flag sets it to `DEBUG`. `FASTMCP_ENABLE_RICH_LOGGING=false` switches to plain/JSON output.

### Log Levels
| Level | Use for |
|-------|---------|
| `DEBUG` | Detailed internals: cache hits, parameter values, config resolution |
| `INFO` | Significant operations: service startup, configuration decisions (tool calls logged by middleware) |
| `WARNING` | Degraded but continuing: API errors with fallback, missing optional config, unexpected data |
| `ERROR` | Failures affecting the primary result. Use `logger.error(..., exc_info=True)` when traceback is needed |

### Exception Handling
- All exceptions must be caught and handled. No bare `except:`. Always specify the exception type.
- Expected errors (HTTP 4xx, missing data): catch, log, return user-facing error string.
- Optional enrichment failures: catch, log at `DEBUG` with `exc_info=True`, continue.
- Primary result errors: catch, log at `WARNING` or `ERROR`, return error string.
- `ErrorHandlingMiddleware` is a safety net. If it catches something, that's a bug to fix.

### Message Format
- Pseudo-structured: `logger.info("event_name key=%s", value)`
- Event name as first token (snake_case), then key=value pairs via `%s` formatting.
- Never use f-strings in log calls (defeats lazy evaluation).
