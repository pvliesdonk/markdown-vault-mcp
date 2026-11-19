"""Logging setup: this module owns the root logger; FastMCP's is turned off.

Ported from ``fastmcp_pvl_core._logging`` 10.2.0 (#2276). The renderer still
comes from pvl-core through the shim until #2342; core's ``SecretMaskFilter``
had no user here and stayed behind.

``configure_logging_from_env`` installs a single console handler chain at
the **root** logger and neutralises FastMCP's own logging
(``fastmcp.settings.log_enabled = False``), rather than delegating to
FastMCP's ``configure_logging``. Every logger — ``fastmcp.*`` included —
propagates into that chain instead of rendering through one of its own.
Rendering is chosen once, process-wide, via ``{PREFIX}_LOG_FORMAT``: the
Rich ``event key=value`` pair of handlers, or a single JSON handler with
no separate traceback path (see :func:`_resolve_format`).

Under HTTP transport, a server started through
:func:`markdown_vault_mcp._serve.run_http`
pins ``log_config=None``, so uvicorn never runs its own ``dictConfig`` and
never reinstalls a handler on ``uvicorn.access``/``uvicorn.error``. Those
loggers stay on the root chain this module installs, so this module is the
sole console owner regardless of transport.

The ``-v`` CLI flag forces ``DEBUG``; otherwise ``<PREFIX>_LOG_LEVEL``
wins, with the legacy ``FASTMCP_LOG_LEVEL`` honoured as a deprecated
fallback for one release when ``<PREFIX>_LOG_LEVEL`` is unset; otherwise
``INFO``.

Three mechanisms keep the operator stream readable at both ends:
``_NOISY_THIRD_PARTY_LOGGERS`` (loud at ``INFO``, demoted),
``_DEBUG_FLOOD_LOGGERS`` (loud at ``DEBUG``, capped), and
``_AccessLogFilter`` (``uvicorn.access``, left at ``NOTSET`` so the level
governs whether access lines appear at all, and always installed — at
``DEBUG`` it keeps every status instead of failures only, but it never
stops redacting, since whether a line is worth logging is a preference and
whether a credential may appear in it is not).
"""

from __future__ import annotations

import logging
import re
import sys

import fastmcp
from rich.console import Console
from rich.logging import RichHandler

from markdown_vault_mcp._env import env
from markdown_vault_mcp._pvl_core import (
    _ACCESS_FIELDS_ATTR,
    JsonFormatter,
    _AccessLogFields,
    _NeverRaiseFilter,
    render_rich,
)

logger = logging.getLogger(__name__)

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_VALID_FORMATS = {"RICH", "JSON"}

_NOISY_THIRD_PARTY_LOGGERS = (
    "mcp.server.lowlevel.server",
    "httpx",
    "httpcore",
)
"""Loggers that are loud at ``INFO`` and quiet at ``WARNING``.

``httpx``/``httpcore`` are here because they arrive through fastmcp, exactly
like ``docket.worker`` below: the same position, so the same owner. Servers
that quieted them locally had done so by contradictory rules that fought
inside one process.

``uvicorn.access`` is deliberately absent: a level cannot express "failures
only", so it gets a filter instead. ``uvicorn.error`` is also absent: it
carries genuine bind / startup failures and is never demoted.
"""

# Third-party loggers with the opposite shape: near-silent at INFO, but a
# per-iteration firehose at DEBUG driven by a poll loop that runs whether or
# not there is any work. ``docket.worker`` (pydocket, which every consumer
# inherits through the ``fastmcp[tasks]`` base dependency) logs two records
# per poll at its 250 ms default check interval — ~2500 lines/minute on a
# permanently idle queue, which buries every first-party DEBUG line. Capped
# at INFO when the root level is DEBUG, so the worker's own lifecycle records
# still come through while the poll trace does not.
_DEBUG_FLOOD_LOGGERS = ("docket.worker",)

_ACCESS_LOGGER = "uvicorn.access"

_QUERY_RE = re.compile(r"[?#]")
_TRANSFER_TOKEN_RE = re.compile(r"(^|/)transfer/[^/]+", re.IGNORECASE)

_ACCESS_ARG_COUNT = 5
_ACCESS_CLIENT_INDEX = 0
_ACCESS_METHOD_INDEX = 1
_ACCESS_PATH_INDEX = 2
_ACCESS_STATUS_INDEX = 4


class _AccessLogFilter(logging.Filter):
    """Redact every request line, and drop successful ones unless asked not to.

    uvicorn emits every access record at ``INFO`` regardless of status, so
    at the default level the successful ones are pure duplication: the
    request-logging middleware already reports each MCP call with its method
    and duration. What the middleware cannot report is what never reached it
    — a 401 refused by auth, a 404, a 413 — so those stay, and a readiness
    probe becomes visible exactly when it starts failing. Whether a
    successful request deserves a line is a preference: *drop_successes*
    expresses it, and ``_apply_access_policy`` ties it to the level so an
    operator at ``DEBUG`` sees every status.

    Redaction is not a preference and is never conditional on
    *drop_successes* — every record this filter is given gets rewritten,
    kept or not, because uvicorn logs the request line as ``path?query``:

    * the query string goes entirely. No route in this family carries
      diagnostic query parameters; the ones that carry anything are the
      OAuth routes, where it is an authorization code or PKCE material.
    * the segment after ``/transfer/`` is masked, because the transfer token
      is in the *path*. An expired link produces the 4xx this filter always
      keeps; a live one produces a 2xx, visible only at ``DEBUG`` — which is
      exactly why redaction cannot be tied to *drop_successes* either.

    A record of any other shape passes untouched: this filter judges
    uvicorn's access line, and anything else on that logger is not its
    business.

    A record this filter understands and keeps also gets the parsed
    ``client``/``method``/``path``/``status`` attached as
    :class:`~._log_render._AccessLogFields`, under
    :data:`~._log_render._ACCESS_FIELDS_ATTR`. uvicorn owns this record's
    template, so it never conforms to the family's log-call grammar and
    :class:`~._log_render.JsonFormatter` would otherwise have nothing but a
    formatted ``message`` to emit. *path* on that attribute is the same
    value just written back into ``record.args`` above — redacted once,
    here, never re-derived by a renderer — so the JSON field and the Rich
    line can never disagree about what the path was.
    """

    def __init__(self, *, drop_successes: bool) -> None:
        super().__init__()
        self._drop_successes = drop_successes

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) != _ACCESS_ARG_COUNT:
            return True
        status = args[_ACCESS_STATUS_INDEX]
        if not isinstance(status, int):
            return True
        path = _QUERY_RE.split(str(args[_ACCESS_PATH_INDEX]), maxsplit=1)[0]
        path = _TRANSFER_TOKEN_RE.sub(r"\1transfer/<redacted>", path)
        record.args = (
            *args[:_ACCESS_PATH_INDEX],
            path,
            *args[_ACCESS_PATH_INDEX + 1 :],
        )
        keep = not (self._drop_successes and status < 400)
        if keep:
            setattr(
                record,
                _ACCESS_FIELDS_ATTR,
                _AccessLogFields(
                    client=str(args[_ACCESS_CLIENT_INDEX]),
                    method=str(args[_ACCESS_METHOD_INDEX]),
                    path=path,
                    status=status,
                ),
            )
        return keep


def _apply_access_policy(level: int) -> None:
    """Install the access filter, leaving exactly one instance either way.

    Always installed — redaction must never depend on verbosity — but the
    survivor's ``drop_successes`` flag tracks the level passed on *this*
    call: remove-then-add means an operator flipping between ``DEBUG`` and
    anything else always ends up with the rule matching their latest call,
    never a stale one from before the flip.
    """
    access = logging.getLogger(_ACCESS_LOGGER)
    for existing in [f for f in access.filters if isinstance(f, _AccessLogFilter)]:
        access.removeFilter(existing)
    # NOTSET, not pinned: the filter decides *which* requests are worth a
    # line; the level decides *whether* the operator wants request lines at
    # all. Left on NOTSET, uvicorn.access inherits the root level, so an
    # operator raising the level to WARNING or above silences every access
    # line — kept or not — before the filter ever sees it.
    access.setLevel(logging.NOTSET)
    access.addFilter(_AccessLogFilter(drop_successes=level != logging.DEBUG))


_OWNED_ATTR = "_pvl_core_owned"
"""Marks the handlers this module installed.

Not needed to recognise our own Rich pair on an ordinary second call:
``_is_console_handler`` falls back to ``RichHandler.console.file``, which
resolves to ``sys.stderr`` exactly like a plain ``StreamHandler``'s
``.stream``, so the console rule below already catches our own pair without
this marker. It still matters in two cases the stream-identity rule cannot
reach on its own: ``sys.stderr`` being ``None`` (Rich substitutes a NULL
file that matches no console-stream identity), and the JSON-mode
``StreamHandler`` whose stream may have been reassigned after
construction. Marking is what makes repeated configuration — and a mode
change between calls — leave exactly one chain at root regardless.
"""


def _console_stream_ids() -> set[int]:
    """Identity of every stream that means "the operator's console"."""
    streams = (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__)
    return {id(stream) for stream in streams if stream is not None}


def _is_console_handler(handler: logging.Handler) -> bool:
    """Whether *handler* writes to the console.

    Keys on stream identity, never on handler type. pytest's
    ``LogCaptureHandler`` is a ``StreamHandler`` subclass over a
    ``StringIO``; a type-based rule would detach ``caplog`` from every test
    in the suite. ``RichHandler`` is the mirror case — not a
    ``StreamHandler`` at all, with its stream at ``console.file``.
    """
    stream = getattr(handler, "stream", None)
    if stream is None:
        stream = getattr(getattr(handler, "console", None), "file", None)
    return stream is not None and id(stream) in _console_stream_ids()


def _neutralise_fastmcp() -> None:
    """Stop FastMCP owning its own logger tree.

    ``log_enabled = False`` makes its ``configure_logging`` return before it
    touches handlers or ``propagate`` — including the call inside
    ``temporary_log_level``, which is what silently reverted the first
    attempt at this (issue #323). Turning the library off is durable where
    fighting it was not.

    The level reset is load-bearing rather than tidiness: FastMCP pins the
    ``fastmcp`` logger to ``INFO`` at import time, and left in place that
    blocks every ``fastmcp.*`` DEBUG record — the request middleware's
    included — before it can reach root.
    """
    fastmcp.settings.log_enabled = False
    fastmcp_logger = logging.getLogger("fastmcp")
    for handler in fastmcp_logger.handlers[:]:
        fastmcp_logger.removeHandler(handler)
    fastmcp_logger.propagate = True
    fastmcp_logger.setLevel(logging.NOTSET)


class _RichTextFormatter(logging.Formatter):
    """Renders a record's message through the family's log-call grammar.

    Overrides ``formatMessage`` rather than ``format``: ``RichHandler``
    always calls ``self.format(record)`` first, but for a record carrying
    ``exc_info`` with ``rich_tracebacks=True`` (the traceback-companion
    handler's whole purpose) it then *discards* that result and calls
    ``formatter.formatMessage(record)`` directly instead, to build the
    message line that accompanies its own Rich-rendered traceback panel
    (see ``rich.logging.RichHandler.emit``). Overriding only ``format``
    would leave that second, actually-rendered path on the default
    ``%(message)s`` substitution instead of :func:`render_rich` — the
    non-exc_info handler would quote a value containing a space, the
    exc_info one would not.

    Delegating to :func:`render_rich` here, instead of leaving the default
    ``%(message)s`` substitution, has two effects. For a conforming record, a
    field value containing whitespace or a quote renders quoted in Rich mode,
    the quoting rule the request middleware applies to its own line and JSON
    mode applies per field: one rule, one place. For a non-conforming record,
    :func:`render_rich` falls back to ``record.getMessage()``, what a plain
    formatter produces.
    """

    def formatMessage(self, record: logging.LogRecord) -> str:
        return render_rich(record)


def _install_root_handlers(level: int, fmt: str) -> None:
    """Make this module the sole owner of the root logger's console output.

    Removes our own handlers and any pre-existing console handler — the
    double-render source when ``opentelemetry-instrument`` has installed
    one — and leaves every other handler untouched, so an operator's OTLP,
    file or syslog handler survives. That tolerance is what closes #323:
    with ``fastmcp.*`` propagating again, a handler attached at root now
    receives every record in the process. The removal runs unconditionally
    of *fmt*, so a mode change (``json`` -> ``rich`` -> ``json``) tears down
    the previous mode's chain the same way a same-mode call does — that is
    what ``_OWNED_ATTR`` is for.

    *fmt* ``"rich"`` installs two handlers, reproducing the pair FastMCP
    installs: tracebacks render compressed (no path or level column,
    framework frames suppressed, three frames) so a stack trace does not
    drown the line that caused it. *fmt* ``"json"`` installs a single
    ``StreamHandler`` with :class:`~._log_render.JsonFormatter` instead —
    no separate traceback handler, because in JSON a traceback is the
    ``exception`` field on the same record, and a second handler would
    print it twice.

    Every handler installed here also gets
    :class:`~._log_render._NeverRaiseFilter`, added before any other
    filter on that handler. It is what makes a log call never raise
    regardless of render mode: ``logging.Formatter.format`` calls
    ``record.getMessage()`` before ``formatMessage`` runs, and
    ``RichHandler.emit`` calls it again on its ``exc_info`` branch — both
    bypass :func:`~._log_render.render_rich` and
    :class:`~._log_render.JsonFormatter` entirely, so only a filter on the
    handler itself, which runs before either path, can rewrite an
    unrenderable record in time.
    """
    root = logging.getLogger()
    for handler in root.handlers[:]:
        if getattr(handler, _OWNED_ATTR, False) or _is_console_handler(handler):
            root.removeHandler(handler)

    if fmt == "json":
        json_handler = logging.StreamHandler(sys.stderr)
        json_handler.setFormatter(JsonFormatter())
        json_handler.addFilter(_NeverRaiseFilter())
        setattr(json_handler, _OWNED_ATTR, True)
        root.addHandler(json_handler)
        root.setLevel(level)
        return

    console = Console(stderr=True)
    formatter = _RichTextFormatter()

    main = RichHandler(console=console)
    main.setFormatter(formatter)
    main.addFilter(_NeverRaiseFilter())
    main.addFilter(lambda record: record.exc_info is None)

    tracebacks = RichHandler(
        console=console,
        show_path=False,
        show_level=False,
        rich_tracebacks=True,
        tracebacks_max_frames=3,
    )
    tracebacks.setFormatter(formatter)
    tracebacks.addFilter(_NeverRaiseFilter())
    tracebacks.addFilter(lambda record: record.exc_info is not None)

    for handler in (main, tracebacks):
        setattr(handler, _OWNED_ATTR, True)
        root.addHandler(handler)
    root.setLevel(level)


def _resolve_level(env_prefix: str, *, verbose: bool) -> tuple[int, bool]:
    """Resolve the level, and say whether the legacy variable supplied it.

    Order: ``verbose`` wins, then ``{PREFIX}_LOG_LEVEL``, then the legacy
    ``FASTMCP_LOG_LEVEL``, then ``INFO``. An unrecognised name falls back to
    ``INFO`` rather than raising — a typo in a log level should not stop a
    server from starting.
    """
    if verbose:
        return logging.DEBUG, False

    raw = env(env_prefix, "LOG_LEVEL")
    # ``env()`` treats an empty value as unset; read the legacy variable
    # through it too (prefix "FASTMCP", name "LOG_LEVEL") so
    # ``FASTMCP_LOG_LEVEL=""`` is unset the same way, instead of firing a
    # deprecation warning for a variable that carries no value.
    legacy = env("FASTMCP", "LOG_LEVEL")
    bridged = raw is None and legacy is not None
    name = (raw if raw is not None else legacy or "INFO").strip().upper()
    if name not in _VALID_LEVELS:
        name = "INFO"
    return getattr(logging, name, logging.INFO), bridged


def _stderr_is_tty() -> bool:
    """Whether ``sys.stderr`` is a terminal, defensively.

    A stream may not have ``isatty`` at all (some wrappers omit it), and a
    closed stream raises when asked. Either failure is treated as "not a
    TTY" — the safe default, because the case that matters is a container,
    where stderr is a pipe and JSON is what a log collector expects.

    A free function rather than an inline ``sys.stderr.isatty()`` call so a
    test can monkeypatch the probe itself: reassigning ``sys.stderr``
    directly would fight pytest's own capture, which already substitutes
    its own stderr object for the duration of a test.
    """
    try:
        return sys.stderr.isatty()
    except Exception:  # broad: any failure means "not a TTY"
        return False


def _resolve_format(env_prefix: str) -> str:
    """Resolve the render mode: ``rich`` or ``json``.

    ``{env_prefix}_LOG_FORMAT`` picks the mode outright when it is one of
    ``rich``/``json`` (case-insensitive) — even on a non-TTY stream, which
    is what lets an operator force human-readable output in a container for
    local debugging, or force JSON at a real terminal for a dry run.
    Unset or unrecognised falls back to auto, the same way an unknown
    ``LOG_LEVEL`` falls back to ``INFO``: a typo in a format name must not
    stop a server from starting. Auto picks ``rich`` when stderr is a TTY,
    ``json`` otherwise.
    """
    raw = env(env_prefix, "LOG_FORMAT")
    name = (raw or "").strip().upper()
    if name in _VALID_FORMATS:
        return name.lower()
    return "rich" if _stderr_is_tty() else "json"


def configure_logging_from_env(env_prefix: str, *, verbose: bool = False) -> None:
    """Configure logging globally based on environment and verbose flag.

    Level resolution order:

    1. If *verbose* is ``True``: force ``DEBUG``.
    2. Otherwise, use ``{env_prefix}_LOG_LEVEL`` if set (case-insensitive).
    3. Otherwise, fall back to the legacy ``FASTMCP_LOG_LEVEL`` if set —
       deprecated for one release; a single warning names the replacement.
    4. Otherwise, default to ``INFO``.

    Unknown level names fall back to ``INFO``. This module owns the root
    logger outright: FastMCP's own ``configure_logging`` is neutralised
    and a single handler chain is installed at root instead, so every
    namespace in the process — FastMCP's included — renders through one
    chain.

    Render mode: ``{env_prefix}_LOG_FORMAT``, case-insensitive.

    - ``"rich"``: the human-readable ``event key=value`` pair of handlers,
      installed even when stderr is not a TTY (e.g. forced for local
      debugging of a containerised process).
    - ``"json"``: a single handler emitting one JSON object per record,
      for a log aggregator. No separate traceback handler — a traceback is
      the ``exception`` field on the same record.
    - Unset or unrecognised: auto — ``rich`` when stderr is a TTY,
      ``json`` otherwise (the case that matters is a container). An
      unrecognised value falls back silently, the same way an unknown
      ``LOG_LEVEL`` falls back to ``INFO``.

    Handler installation at root holds three invariants:

    1. **stderr only.** Nothing this function installs writes to stdout,
       which is the protocol channel under stdio transport.
    2. **Idempotent.** Handlers this function installed are marked and
       removed before reinstalling on every call, so repeated calls leave
       exactly one chain at root.
    3. **Exclusive over the console, tolerant of everything else.** Any
       pre-existing root handler that writes to the console is replaced
       (the double-render source when ``opentelemetry-instrument`` has
       installed one); every other handler — an OTLP ``LoggingHandler``, a
       file handler, a syslog handler — is left untouched. Because
       ``fastmcp.*`` now propagates instead of rendering through its own
       handlers, a handler an operator attached at root also starts
       receiving ``fastmcp.*`` records.

    Three noisy third-party loggers — ``mcp.server.lowlevel.server`` (the MCP
    SDK request line), ``httpx``, and ``httpcore`` — are demoted to
    ``WARNING`` (or the resolved level itself, if that is stricter than
    ``WARNING``) whenever the resolved level is above ``DEBUG``, so their
    per-request chatter stays out of the default ``INFO`` stream without
    ever making them *louder* than the operator's own chosen level. At
    ``DEBUG`` they are reset to ``NOTSET`` and reappear. ``uvicorn.error``
    is never demoted.

    ``uvicorn.access`` (the HTTP access log) is handled differently: a
    level cannot express "failures only", so it is left at ``NOTSET`` —
    inheriting the root level, like any other unconfigured logger — and
    always given a filter that redacts the query string and any
    ``/transfer/<token>`` segment from every request line it sees. The
    filter decides *which* requests are worth a line; the level decides
    *whether* the operator wants request lines at all — raising
    ``{env_prefix}_LOG_LEVEL`` to ``WARNING`` or above silences access lines
    entirely, kept or not, the same way it silences every other logger
    without unique verbosity needs. At every level except ``DEBUG`` the
    filter also drops successful (``< 400``) requests; at ``DEBUG`` every
    status passes, but the redaction never stops — whether a line is worth
    logging is a preference the level and filter both express, whether a
    credential may appear in it is not.

    One third-party logger is capped in the other direction:
    ``docket.worker`` is pinned to ``INFO`` when the resolved level is
    ``DEBUG``, because its poll loop emits a record per iteration even on
    an idle queue and would otherwise dominate the ``DEBUG`` stream. Its
    ``INFO`` lifecycle records are unaffected, and at every other level it
    is left at ``NOTSET`` — the cap only ever removes the poll trace. An
    operator debugging the task queue itself restores the full stream
    after this call with
    ``logging.getLogger("docket.worker").setLevel(logging.DEBUG)``.

    Args:
        env_prefix: Caller-supplied identity, not a default: the
            server's own env var prefix (e.g. ``"MY_APP"``), matching
            ``ServerConfig.from_env(env_prefix)``. Used to read
            ``{env_prefix}_LOG_LEVEL``. Required, positional.
        verbose: CLI flag, not an env var — the caller's ``-v``/``--verbose``
            switch, passed through as a keyword. If ``True``, forces
            ``DEBUG`` (overrides both ``{env_prefix}_LOG_LEVEL`` and
            ``FASTMCP_LOG_LEVEL``).
    """
    level, bridged = _resolve_level(env_prefix, verbose=verbose)
    fmt = _resolve_format(env_prefix)
    _neutralise_fastmcp()
    _install_root_handlers(level, fmt)

    # max(WARNING, level), not a flat WARNING: at ERROR/CRITICAL a flat
    # WARNING would *raise* the effective level for these loggers above what
    # the operator chose, making them louder than the server's own code.
    noisy_level = (
        logging.NOTSET if level == logging.DEBUG else max(logging.WARNING, level)
    )
    for name in _NOISY_THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(noisy_level)

    # Mirror image of the demotion above: pin at INFO exactly where the root
    # would otherwise let the poll trace through, and hand the logger back to
    # inheritance everywhere else so repeated calls leave no residue.
    flood_level = logging.INFO if level == logging.DEBUG else logging.NOTSET
    for name in _DEBUG_FLOOD_LOGGERS:
        logging.getLogger(name).setLevel(flood_level)

    _apply_access_policy(level)

    if bridged:
        # Emitted last, deliberately: the handlers that carry it are
        # installed above. A deprecation notice nobody can see is worse
        # than none, because it reads as if the migration were silent.
        # logged at max(WARNING, level) rather than a flat WARNING: an
        # operator running at ERROR or CRITICAL has raised the bar above
        # WARNING, and a flat WARNING would be silently dropped by their own
        # chosen level — never seeing the notice at all.
        logger.log(
            max(logging.WARNING, level),
            "log_level_env_deprecated old=FASTMCP_LOG_LEVEL new=%s_LOG_LEVEL",
            env_prefix.rstrip("_"),
        )
