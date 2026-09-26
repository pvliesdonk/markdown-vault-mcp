---
name: designing-tool-outcomes
description: >-
  Use when writing or changing an MCP tool that can fail, refuse, find nothing or hit a conflict: choosing between returning a result and raising ToolError, writing a tool's error text, choosing log levels for tool outcomes, or when a tool call logs ERROR or a traceback for something that is not a server fault.
---

<!-- ===== TEMPLATE-OWNED — re-rendered on template updates. ===== -->

# Designing tool outcomes

A tool call ends in one of four outcomes. Classify each case before you write
its code. The class fixes three things at once: what the model receives, what
the message tells it to do next, and the log level. The evidence behind every
rule is in `docs/design/reference/mcp-tool-outcomes-and-errors.md` and
`docs/design/reference/negative-outcomes-and-faults.md`.

## The four outcomes

The **contract** is what the tool's description and return type promise. An
outcome that fits the contract is a result. An outcome that cannot produce the
promised thing is an error, however ordinary it is. The two texts split the
work: the description states the contract, limits that hold for every call
included, and never lists failures (`writing-model-facing-text`, "The
contract, not the failures"). So the error text is the one place a failure
is explained, and it has to stand on its own.

| # | Outcome | Examples | Code | The message tells the model | Log |
|---|---|---|---|---|---|
| 1 | Contract met | the note's text; an empty list from a search that ran to completion | `return` the value | nothing extra | the middleware's completion line |
| 2 | Change the request | no note at that path; invalid input; path outside the store; write refused until `if_match` is passed; a feature this deployment leaves off (a read-only vault, an opt-in feature not enabled) | `raise ToolError(msg, log_level=logging.INFO)` | what was wrong and what to call or pass instead; repeating the same call will not help | INFO |
| 3 | Refresh, then retry | stale version or etag; conflicting concurrent change | `raise ToolError(msg, log_level=logging.INFO)` | read again, reapply the change, retry with the new token | INFO |
| 4 | The server failed | I/O error; permissions on the server's own files; a stored file the server cannot parse; index or dependency unavailable; required configuration left out; a bug | let it reach the boundary, or `raise ToolError(msg)` after logging | the request was fine; retry later (transient) or tell the user (needs an operator); do not change strategy | WARNING if it heals itself, else ERROR with the traceback |

The log level answers one question: who has to act? Only the model (2, 3):
INFO. Nobody, because it heals itself: WARNING. An operator: ERROR.

Configuration the server needs and did not get is broken: outcome 4. A
feature the operator left off is a choice, and opt-in features are off by
default: the model works within the deployment it has, so it is outcome 2.

`ToolError`'s default `log_level` is ERROR. Leaving it off an outcome 2 or 3
makes every mistyped path an operator alert.

## Shaping the contract

- Build absence into the contract only where callers routinely expect it:
  searches, lists, existence checks. Keep a heavily used single-item contract
  such as `read` simple. Its not-found is outcome 2, and the tool description
  already steers the model ("look paths up first").
- An empty result means the operation ran and found nothing. If a failure
  stopped it from finding anything, that is outcome 4. Never return `[]` for it.
- Do not return `ToolResult(is_error=True)`. FastMCP and the request-logging
  middleware record it as a completed call, so a real failure disappears from
  the failure logs.

## Writing the message

The model reads the error text once, straight after the call, and acts on it
next. Assume it has nothing else: the tool description may have been
deferred by tool search, cut at 2,048 characters or read many turns ago, and
it does not mention this failure anyway.

- **Outcomes 2 and 3**: what was wrong, naming the argument and quoting the
  value the model sent; then the exact next step, by tool name and parameter
  name. For outcome 3, also say that the same call will fail again and where
  the fresh value comes from. `No note at 'inbox/todo.md'. Find the path with
  search_notes.` `Note 'plan.md' changed since version 'a1b2', so resending
  this call fails again. Call read_note for the current text and version,
  reapply the edit, and pass the new version as if_match.`
- **Outcome 4**: the request was fine, nothing about it should change; retry
  later if the condition heals itself, otherwise tell the user. No cause the
  model cannot act on. `tool_boundary`'s fixed message is the pattern.
- **Self-contained**: never "see the description", "as documented" or an
  error code the model has to look up. In outcomes 2 and 3, name the tool and
  the parameter, even when it is the tool just called.
- **Written to the model**: the same exclusions as a description. No
  exception class, server path, stack frame, log line, environment variable
  or CLI command: the model cannot catch, open, set or run any of them. What
  an operator needs goes in the log record at the same `raise`.
- **Short**: one or two sentences. The message says how to recover from this
  failure. It does not restate the contract.

## The boundary

No exception may reach FastMCP's own handler, which logs ERROR with a
traceback. Catch the domain exceptions that mean outcome 2 or 3 at the call
site and raise them as INFO `ToolError`s. Wrap every tool in pvl-core's
`tool_boundary` so that anything else becomes outcome 4.
`mask_error_details` stays as a safety net, not the handler.

```python
import logging

from fastmcp.exceptions import ToolError
from fastmcp_pvl_core import tool_boundary


@mcp.tool
@tool_boundary
def read_note(path: str) -> dict:
    """Return a note's text and version; look paths up with search_notes first."""
    try:
        note = store.read(path)
    except FileNotFoundError:
        raise ToolError(
            f"No note at '{path}'. Find the path with search_notes.",
            log_level=logging.INFO,
        ) from None
    return {"text": note.text, "version": note.version}
```

Put `@mcp.tool` above `@tool_boundary`. It passes a FastMCP error, and a
missing-client-capability protocol error, through unchanged. An upstream
rate limit or timeout becomes a WARNING and a "retry" message. Anything
else it logs once as `tool_failed` at ERROR with the traceback and replaces
with a fixed message: the request was fine, retry later, tell the user if
it keeps failing. The wrapper keeps the signature,
so the input and output schemas, `Context` injection and `task=`
registration are unchanged. A server-side condition the model can name more
precisely than the generic message, such as "the index is rebuilding, retry
in a minute", gets its own outcome 4 `ToolError` at the call site, logged
at WARNING when it heals itself. `tests/test_tool_outcomes.py` fails any
registered tool without the boundary, app-only and hidden tools included.
Why it is a pvl-core helper and not a per-server copy is
[pvl-core ADR 0005](https://github.com/pvliesdonk/fastmcp-pvl-core/blob/main/docs/adr/0005-tool-boundary.md).

The request-logging middleware logs `tool_call_failed` at the `ToolError`'s
`log_level`, so an outcome 2 or 3 produces no ERROR line anywhere.

## Hooks that end a pvl-core tool

A domain hook that runs inside a tool pvl-core registers follows the same
rule: raise `ToolError(msg, log_level=logging.INFO)` for an outcome the
model can act on, and let anything else be a fault. The transfer
`validate` hook rejects a ref this way; a `ValueError` from it is reported
to the model as a server-side error. So is anything but a `ToolError` from
a `register_long_running_tool` coroutine, before or after its deadline.

## Common mistakes

| Mistake | Fix |
|---|---|
| `raise ToolError(msg)` for not-found, a bad path or a stale version | Pass `log_level=logging.INFO`: outcome 2 or 3. |
| Relying on `mask_error_details=True` to handle unexpected exceptions | Wrap the tool in pvl-core's `tool_boundary`. |
| A server-side failure message that only says what happened ("not permitted to access X") | Add the strategy: retry later, or tell the user. |
| The same class logged at INFO in one tool and WARNING or ERROR in another | Take the level from the table's "who acts" rule, not from how alarming the exception name sounds. |
| Returning `[]` or `None` when the lookup itself failed | Outcome 4. |
| A tool description that lists its errors or says what to do on one | Move it into the `ToolError` text; the description keeps the contract. |
| Error text naming an exception class, a server path or an env var | Say what the model should change or call instead; log the detail. |
| Error text pointing at the description, or at an error code | Say the next step in the message itself, by tool and parameter name. |
| Returning an error string or `{"error": ...}` for an outcome the contract does not promise | Raise `ToolError` (outcomes 2 to 4). A status field is outcome 1 only when the declared return type includes it. |
