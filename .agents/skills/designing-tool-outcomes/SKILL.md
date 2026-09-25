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
promised thing is an error, however ordinary it is.

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

## The boundary

No exception may reach FastMCP's own handler, which logs ERROR with a
traceback. Catch the domain exceptions that mean outcome 2 or 3 at the call
site and raise them as INFO `ToolError`s. Wrap every tool so that anything else
becomes outcome 4. `mask_error_details` stays as a safety net, not the handler.

```python
import functools
import inspect
import logging

from fastmcp.exceptions import ToolError

logger = logging.getLogger(__name__)

_FAULT = (
    "{tool} failed because of a server-side error; the request was fine. "
    "Retry later, and tell the user if it keeps failing."
)


def tool_boundary(fn):
    """Turn any exception that is not already a ToolError into outcome 4."""

    def fault() -> ToolError:
        logger.exception("tool_failed tool=%s", fn.__name__)
        return ToolError(_FAULT.format(tool=fn.__name__))

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except ToolError:
                raise
            except Exception as exc:
                raise fault() from exc

    else:

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ToolError:
                raise
            except Exception as exc:
                raise fault() from exc

    return wrapper


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

Put `@mcp.tool` above `@tool_boundary`. `functools.wraps` keeps the signature,
so the input and output schemas are unchanged. A server-side condition the
model can name more precisely than the generic message, such as "the index is
rebuilding, retry in a minute", gets its own outcome 4 `ToolError` at the call
site, logged at WARNING when it heals itself.

## Common mistakes

| Mistake | Fix |
|---|---|
| `raise ToolError(msg)` for not-found, a bad path or a stale version | Pass `log_level=logging.INFO`: outcome 2 or 3. |
| Relying on `mask_error_details=True` to handle unexpected exceptions | Wrap the tool in `tool_boundary`. |
| A server-side failure message that only says what happened ("not permitted to access X") | Add the strategy: retry later, or tell the user. |
| The same class logged at INFO in one tool and WARNING or ERROR in another | Take the level from the table's "who acts" rule, not from how alarming the exception name sounds. |
| Returning `[]` or `None` when the lookup itself failed | Outcome 4. |
| Returning an error string or `{"error": ...}` for an outcome the contract does not promise | Raise `ToolError` (outcomes 2 to 4). A status field is outcome 1 only when the declared return type includes it. |
