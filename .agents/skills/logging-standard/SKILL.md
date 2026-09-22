---
name: logging-standard
description: >-
  Use before adding or changing any logging call in src/: log levels, exception handling, the event_name key=value message grammar tests/test_logging_standard.py enforces, and what pvl-core's root-logger setup (standard-library logging rendered as Rich or JSON) does with a call that follows it or does not.
---

<!-- ===== TEMPLATE-OWNED — re-rendered on template updates. ===== -->

# Logging Standard

## Logging Standard

### Scope

This standard governs **first-party code only** — `src/markdown_vault_mcp/`
and `tests/`. Two categories of log output are explicitly **out of scope**;
do not try to make them conform, silence them, or reformat them:

- **pvl-core's request-logging middleware**: emits conforming
  `event key=value` records for every MCP message (`request_started` /
  `request_completed` / `request_failed` keyed by `method=`, and the
  `tool_call_*` vocabulary carrying `tool=<name>`). Governed by
  `fastmcp-pvl-core`; no first-party code needed.
- **Third-party loggers** — `uvicorn.access`, `uvicorn.error`,
  `mcp.server.lowlevel.server`, `httpx`, `httpcore`, `docket.worker`:
  `configure_logging_from_env` governs them for the whole family, and a
  second rule on the same logger in this repository would only conflict
  with it. What it does: `mcp.server.lowlevel.server`, `httpx` and
  `httpcore` sit at `WARNING` (or the configured level, if stricter) at
  every level above `DEBUG`, and at `NOTSET` under `DEBUG` so they
  reappear. `uvicorn.access` is *filtered*, not levelled: it stays at
  `NOTSET`, only failed requests (status 400 and above) pass below
  `DEBUG`, every status passes at `DEBUG`, and at every level the query
  string is stripped and a `/transfer/<token>` segment is redacted — a
  credential in a request line is not a preference. `uvicorn.error` is
  never demoted (it carries bind failures). `docket.worker` is capped at
  `INFO` under `DEBUG` so its poll loop does not flood the stream.

### Framework

- Standard library `logging` throughout. Every module:
  `logger = logging.getLogger(__name__)`.
- No `print()` for operational output. No third-party logging libraries;
  there is no structlog here, and the JSON output below is pvl-core's
  formatter over ordinary `LogRecord`s.
- pvl-core's middleware handles tool invocation, timing and error logging
  automatically.
- **pvl-core owns the root logger.** `cli._root` and `make_server()` call
  `configure_logging_from_env("MARKDOWN_VAULT_MCP", verbose=...)`; it installs
  the one console handler chain at root, switches FastMCP's own handlers
  off so `fastmcp.*` propagates into that chain, and is idempotent (its
  own handlers are replaced on every call, any other root handler — OTLP,
  file, syslog — is left alone). Never attach a handler yourself: a
  second console handler renders every line twice. Everything it writes
  goes to stderr; stdout is the protocol channel under stdio transport.

### Operator surface

| Variable | Effect |
|---|---|
| `MARKDOWN_VAULT_MCP_LOG_LEVEL` | The level for every logger in the process. Default `INFO`; `-v` on the command line forces `DEBUG`. The unprefixed `FASTMCP_LOG_LEVEL` still works for one major and logs `log_level_env_deprecated` once. |
| `MARKDOWN_VAULT_MCP_LOG_FORMAT` | `rich` (one coloured `event key=value` line per record) or `json` (one JSON object per record). Unset is auto: `rich` when stderr is a terminal, `json` everywhere else. An unrecognised value falls back to auto, the same way an unknown level falls back to `INFO`. |
| `MARKDOWN_VAULT_MCP_SHUTDOWN_GRACE_S` | The SIGTERM drain window `run_http` gives uvicorn; default 3. Not a logging knob, listed because `cli.serve` used to hard-code it. |

The container image, the packaged systemd unit and the compose file set
neither logging variable: a container and journald are not terminals, so
auto already means JSON there, and `.env` or `/etc/markdown-vault-mcp/env`
keeps the choice.

**Under pytest the server logs JSON**, because a test runner's stderr is
not a terminal either. A test that asserts on Rich-shaped stderr sets
`MARKDOWN_VAULT_MCP_LOG_FORMAT=rich` with `monkeypatch` before the code under
test calls `configure_logging_from_env`, or — better — asserts on
`caplog.records` (`record.msg` and `record.args`), which no renderer touches.

### Log Levels

| Level | Use for |
|-------|---------|
| `DEBUG` | Detailed internals: cache hits, parameter values, config resolution |
| `INFO` | Significant operations: service startup, configuration decisions (tool calls logged by middleware) |
| `WARNING` | Degraded but continuing: API errors with fallback, missing optional config, unexpected data |
| `ERROR` | Failures affecting the primary result. Use `logger.error(..., exc_info=True)` when traceback is needed |
| `CRITICAL` | The process cannot continue to do its job. The level parser accepts it for `MARKDOWN_VAULT_MCP_LOG_LEVEL` too; at that level even the deprecation notice above is raised to `CRITICAL` so it stays visible |

### Exception Handling

- All exceptions must be caught and handled. No bare `except:`. Always specify the exception type.
- Expected errors (HTTP 4xx, missing data): catch, log, return user-facing error string.
- Optional enrichment failures: catch, log at `DEBUG` with `exc_info=True`, continue.
- Primary result errors: catch, log at `WARNING` or `ERROR`, return error string.
- `ErrorHandlingMiddleware` is a safety net. If it catches something, that's a bug to fix.

### Message Format

Pseudo-structured: `logger.info("event_name key=%s", value)`. Standard
library logging keeps the template (`record.msg`) and the values
(`record.args`) apart on the record, so a consumer that parses the
*template* recovers typed fields from every call, but only when the
template follows the grammar below. `fastmcp-pvl-core` owns that grammar
and ships the check; `tests/test_logging_standard.py` runs it over `src/`
and fails the build on any call that breaks it.

**Why it is a rule and not a preference.** In JSON mode a conforming record
renders as `{"ts": …, "level": …, "logger": …, "event": "<name>",
"<field>": <value>, …}` — one key per field, ready for a collector's facets.
A non-conforming call does not error: it renders as an opaque
`"message": "<the formatted text>"` and every field is lost. In Rich mode
both shapes look alike, which is why the difference is invisible at a
terminal and matters in production.

**The grammar.** A template is an event name, then zero or more
space-separated fields:

- **Event name**: the first token, `snake_case` (`[a-z][a-z0-9_]*`).
  Nothing before it.
- **Field**: `name=value`, where `name` is `snake_case` and `value` is
  either one `%`-conversion (`%s`, `%d`, `%i`, `%f`, `%.1f`, `%r`, `%x`,
  `%e`, `%g` and their upper-case forms, with optional flags, width and
  precision) or a fixed token with no space, `%` or `=` in it
  (`status=configured`).
- **Arguments**: exactly one positional argument per conversion, in order.
  No `*args`; the count must be readable from the source.
- **Nothing else**: no prose before, between or after the fields; no
  colons, dashes, parentheses or units outside a field; no `%%`.

**Cases the grammar settles.** Each left column is a real call from the
family, each right column is the conforming form:

| Do not write | Write |
|---|---|
| `"Service started"` | `"service_started"` |
| `"Auth enabled: mode=%s"` | `"auth_enabled mode=%s"` |
| `"OllamaProvider initialised: host=%s"` | `"ollama_provider_initialised host=%s"` |
| `"s2_rate_limited attempt=%d/%d"` | `"s2_rate_limited attempt=%d max_attempts=%d"` |
| `"s2_rate_limited waiting=%.1fs"` | `"s2_rate_limited waiting_s=%.1f"` |
| `"cache_hit ratio=%d%%"` | `"cache_hit ratio_pct=%d"` |
| `"scaffold inactive (no app_domain)"` | `"apps_scaffold_inactive reason=no_app_domain"` |
| `f"fetch_failed url={url}"` | `"fetch_failed url=%s", url` |

A compound value becomes two fields; a unit or a percent sign moves into
the field name; a reason that used to be prose becomes a fixed-token
value; a traceback goes through `exc_info=True`, never into the message.

**What the renderer does with a conforming call.**

- Four field names are reserved by the JSON envelope: `ts`, `level`,
  `logger` and `event`. A field with one of those names is emitted as
  `field_<name>` (`level=%s` becomes `"field_level"`) so it cannot
  overwrite the record's real severity. Pick another name before an
  aggregator's severity facet surprises you.
- Values need no pre-quoting. A value with whitespace or a `"` is quoted
  and escaped in Rich output and is an ordinary JSON string in JSON mode.
- A malformed call does not raise. `logger.info("e a=%d", "x")` renders
  `<unrenderable log record: <logger name>>` — safe, but the message is
  gone, so the shape of that line is worth recognising in a log.

**Rules the check relies on.**

- Never use f-strings in log calls (defeats lazy evaluation, and hides the
  fields from the parser).
- The logger is the module-level `logger = logging.getLogger(__name__)`
  and the call is one of `debug` / `info` / `warning` / `error` /
  `exception` / `critical` on it. The check judges only that shape: a
  logger reached as `self.logger` or imported from another module, and
  `logger.log(level, ...)`, are invisible to it. That is a limit of the
  check, not permission to use those forms to route around it; following
  the mandated receiver is also what keeps your calls checkable.
- `find_nonconforming_log_calls` is pvl-core's own checker and gates
  pvl-core's build too (pvliesdonk/fastmcp-pvl-core#328). Run
  `uv run pytest tests/test_logging_standard.py -q` to list every
  non-conforming call with its file, line and reason.
