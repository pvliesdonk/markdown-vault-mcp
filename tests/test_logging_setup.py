"""``configure_logging_from_env`` (#2276).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_logging.py``, without its
``SecretMaskFilter`` class of tests: that filter had no user here and was not
ported.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
from typing import TYPE_CHECKING

import pytest
import uvicorn
import uvicorn.config
from rich.logging import RichHandler

from markdown_vault_mcp import _logging_setup as _logging_mod
from markdown_vault_mcp._logging_setup import configure_logging_from_env

if TYPE_CHECKING:
    from collections.abc import Iterator

# Every test in this module reconfigures logging; put the tree back.
pytestmark = pytest.mark.usefixtures("restore_logging_topology")


def _record(msg: str, args: tuple[object, ...] | None = None) -> logging.LogRecord:
    return logging.LogRecord(
        name="x",
        level=logging.DEBUG,
        pathname="_",
        lineno=0,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_sets_debug_when_verbose_true(monkeypatch):
    monkeypatch.delenv("TEST_MCP_LOG_LEVEL", raising=False)
    monkeypatch.delenv("FASTMCP_LOG_LEVEL", raising=False)
    configure_logging_from_env("TEST_MCP", verbose=True)
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG


def test_respects_prefixed_level(monkeypatch):
    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "WARNING")
    configure_logging_from_env("TEST_MCP")
    assert logging.getLogger().getEffectiveLevel() == logging.WARNING


def test_defaults_to_info_when_nothing_set(monkeypatch):
    monkeypatch.delenv("TEST_MCP_LOG_LEVEL", raising=False)
    monkeypatch.delenv("FASTMCP_LOG_LEVEL", raising=False)
    configure_logging_from_env("TEST_MCP")
    assert logging.getLogger().getEffectiveLevel() == logging.INFO


def test_lowercase_level_name_handled(monkeypatch):
    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "warning")
    configure_logging_from_env("TEST_MCP")
    assert logging.getLogger().getEffectiveLevel() == logging.WARNING


def test_unknown_level_falls_back_to_info(monkeypatch):
    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "BOGUS")
    configure_logging_from_env("TEST_MCP")
    assert logging.getLogger().getEffectiveLevel() == logging.INFO


def _bridge_warnings(caplog) -> list[logging.LogRecord]:
    """The deprecation records naming the legacy variable.

    Shared because five tests ask the same question of ``caplog`` — how many
    times, and at what severity, an operator was told the old variable is
    going away.
    """
    return [r for r in caplog.records if "FASTMCP_LOG_LEVEL" in r.getMessage()]


def test_bridges_fastmcp_log_level_with_one_warning(monkeypatch, caplog):
    monkeypatch.delenv("TEST_MCP_LOG_LEVEL", raising=False)
    monkeypatch.setenv("FASTMCP_LOG_LEVEL", "WARNING")
    with caplog.at_level(logging.WARNING):
        configure_logging_from_env("TEST_MCP")
        assert logging.getLogger().getEffectiveLevel() == logging.WARNING
    (warning,) = _bridge_warnings(caplog)
    assert "TEST_MCP_LOG_LEVEL" in warning.getMessage()


def test_bridges_fastmcp_log_level_at_error_severity(monkeypatch, caplog):
    # Regression: a flat logger.warning(...) is silently dropped by an
    # operator's own ERROR/CRITICAL level (WARNING < ERROR), so the
    # deprecation notice never reaches them. It must be logged at whatever
    # severity is at least as high as the level the operator chose.
    monkeypatch.delenv("TEST_MCP_LOG_LEVEL", raising=False)
    monkeypatch.setenv("FASTMCP_LOG_LEVEL", "ERROR")
    with caplog.at_level(logging.ERROR):
        configure_logging_from_env("TEST_MCP")
        assert logging.getLogger().getEffectiveLevel() == logging.ERROR
    (warning,) = _bridge_warnings(caplog)
    assert warning.levelno == logging.ERROR


def test_empty_legacy_log_level_is_not_bridged(monkeypatch, caplog):
    # env() treats an empty prefixed value as unset; the legacy variable
    # must be read the same way, or FASTMCP_LOG_LEVEL="" fires a spurious
    # deprecation warning for a variable that carries no value.
    monkeypatch.delenv("TEST_MCP_LOG_LEVEL", raising=False)
    monkeypatch.setenv("FASTMCP_LOG_LEVEL", "")
    with caplog.at_level(logging.WARNING):
        configure_logging_from_env("TEST_MCP")
        assert logging.getLogger().getEffectiveLevel() == logging.INFO
    assert _bridge_warnings(caplog) == []


def test_prefixed_level_wins_and_is_silent(monkeypatch, caplog):
    # The effective-level assertion must live inside the `with` block:
    # caplog.at_level() restores the root logger's *pre-with* level on
    # exit, which would otherwise mask the level configure_logging_from_env
    # actually installed. caplog.records is unaffected by that restore, so
    # the warning-absence check is fine outside.
    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "ERROR")
    monkeypatch.setenv("FASTMCP_LOG_LEVEL", "DEBUG")
    with caplog.at_level(logging.WARNING):
        configure_logging_from_env("TEST_MCP")
        assert logging.getLogger().getEffectiveLevel() == logging.ERROR
    assert _bridge_warnings(caplog) == []


def test_verbose_overrides_both_and_is_silent(monkeypatch, caplog):
    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "ERROR")
    monkeypatch.setenv("FASTMCP_LOG_LEVEL", "ERROR")
    with caplog.at_level(logging.WARNING):
        configure_logging_from_env("TEST_MCP", verbose=True)
        assert logging.getLogger().getEffectiveLevel() == logging.DEBUG
    assert _bridge_warnings(caplog) == []


def test_verbose_no_longer_writes_the_fastmcp_env_var(monkeypatch):
    monkeypatch.delenv("FASTMCP_LOG_LEVEL", raising=False)
    configure_logging_from_env("TEST_MCP", verbose=True)
    assert os.environ.get("FASTMCP_LOG_LEVEL") is None


def test_env_prefix_is_required():
    with pytest.raises(TypeError):
        configure_logging_from_env()  # type: ignore[call-arg]


def test_stderr_is_tty_false_when_stream_lacks_isatty(monkeypatch):
    # Here monkeypatching sys.stderr itself is correct — unlike the
    # format-selection tests below, this exercises the probe directly
    # rather than routing through configure_logging_from_env, so there is
    # no test harness stderr substitution to fight.
    monkeypatch.setattr(_logging_mod.sys, "stderr", object())
    assert _logging_mod._stderr_is_tty() is False


def test_stderr_is_tty_false_when_isatty_raises(monkeypatch):
    class _ClosedStream:
        def isatty(self) -> bool:
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(_logging_mod.sys, "stderr", _ClosedStream())
    assert _logging_mod._stderr_is_tty() is False


def test_stderr_is_tty_true_when_isatty_says_so(monkeypatch):
    class _TtyStream:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(_logging_mod.sys, "stderr", _TtyStream())
    assert _logging_mod._stderr_is_tty() is True


def _owned_handlers() -> list[logging.Handler]:
    return [
        h for h in logging.getLogger().handlers if getattr(h, "_pvl_core_owned", False)
    ]


@contextlib.contextmanager
def _only_owned_handlers_at_root() -> Iterator[None]:
    """Detach every root handler this module did not install, for the call.

    pytest's own ``LogCaptureHandler`` (behind ``caplog``) sits on root
    too, unprotected by :class:`_NeverRaiseFilter` (the log renderer's)
    — it is exactly the "handler an operator attached to root" the
    guarantee does not cover, and it calls ``record.getMessage()`` itself
    while formatting for its own buffer, which raises straight through it
    for a genuinely malformed record regardless of what pvl-core's own
    handlers do. Detaching it for the scope of a never-raise assertion
    isolates the thing actually under test — the chain
    ``_install_root_handlers`` built — from that unrelated interference,
    without weakening the assertion: the handlers left in place are the
    real objects installed by :func:`configure_logging_from_env`.
    """
    root = logging.getLogger()
    other = [h for h in root.handlers if not getattr(h, "_pvl_core_owned", False)]
    for handler in other:
        root.removeHandler(handler)
    try:
        yield
    finally:
        for handler in other:
            root.addHandler(handler)


def test_format_json_installs_a_single_json_handler(monkeypatch, capsys):
    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "json")
    configure_logging_from_env("TEST_MCP")
    owned = _owned_handlers()
    assert len(owned) == 1
    assert not isinstance(owned[0], RichHandler)

    logging.getLogger("some.domain.module").info("probe key=%s", "value")
    line = capsys.readouterr().err.strip()
    payload = json.loads(line)  # every record on root is one parseable object
    assert payload["event"] == "probe"
    assert payload["key"] == "value"


def test_format_rich_installs_the_pair_on_a_non_tty(monkeypatch):
    # A CI runner's stderr is not a TTY; an operator setting LOG_FORMAT=rich
    # explicitly must still get Rich there, since the whole point of the
    # override is to skip auto-detection.
    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "rich")
    monkeypatch.setattr(
        "markdown_vault_mcp._logging_setup._stderr_is_tty", lambda: False
    )
    configure_logging_from_env("TEST_MCP")
    owned = _owned_handlers()
    assert len(owned) == 2
    assert all(isinstance(h, RichHandler) for h in owned)


def test_format_rich_renders_conforming_records_via_render_rich(monkeypatch, capsys):
    # Rich mode's non-exc_info handler must render through render_rich, not
    # a bare "%(message)s" substitution: a value containing a space is only
    # quoted if render_rich's field-rendering rule was actually applied.
    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "rich")
    configure_logging_from_env("TEST_MCP")
    logging.getLogger("some.domain.module").warning("probe key=%s", "has space")
    err = capsys.readouterr().err
    assert 'key="has space"' in err


def test_format_rich_traceback_handler_renders_via_render_rich_too(monkeypatch, capsys):
    # RichHandler's rich_tracebacks path bypasses Formatter.format() for an
    # exc_info record and calls formatter.formatMessage() directly to build
    # the message line beside its own traceback panel (see
    # rich.logging.RichHandler.emit) — a second code path that the
    # non-exc_info test above does not exercise, and the one _RichTextFormatter
    # exists to cover by overriding formatMessage rather than format.
    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "rich")
    configure_logging_from_env("TEST_MCP")
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("some.domain.module").exception("probe key=%s", "has space")
    err = capsys.readouterr().err
    assert 'key="has space"' in err


def test_never_raise_filter_rich_mode_survives_arg_count_mismatch(monkeypatch, capsys):
    # Reproduces the installed-path bug directly: logging.Formatter.format
    # calls record.getMessage() *before* formatMessage ever runs, so an
    # argument %d cannot format used to raise a TypeError straight out of
    # this call, bypassing render_rich's own never-raise guarantee
    # entirely (RichHandler.emit never reaches _RichTextFormatter at all
    # in that case). Only a filter attached to the handler itself, which
    # runs before format(), can fix that — see _NeverRaiseFilter.
    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "rich")
    configure_logging_from_env("TEST_MCP")
    with _only_owned_handlers_at_root():
        logging.getLogger("some.domain.module").info("e a=%d", "x")  # must not raise
    err = capsys.readouterr().err
    assert "unrenderable log record" in err


def test_never_raise_filter_json_mode_survives_arg_count_mismatch(monkeypatch, capsys):
    # JSON mode never called getMessage() early, so it never raised to the
    # caller — but the same bad record used to render as typed fields with
    # the unformatted raw argument (JSON mode ignores the %-conversion,
    # see _conforming), silently hiding the caller's mistake instead of
    # flagging it the same way Rich mode now does.
    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "json")
    configure_logging_from_env("TEST_MCP")
    with _only_owned_handlers_at_root():
        logging.getLogger("some.domain.module").info("e a=%d", "x")  # must not raise
    line = capsys.readouterr().err.strip()
    payload = json.loads(line)
    assert "unrenderable log record" in payload["message"]


def test_never_raise_filter_json_mode_survives_getmessage_raising_on_msg_str(
    monkeypatch, capsys
):
    class _RaisingStr:
        def __str__(self) -> str:
            raise RuntimeError("str exploded")

    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "json")
    configure_logging_from_env("TEST_MCP")
    with _only_owned_handlers_at_root():
        logging.getLogger("some.domain.module").info(_RaisingStr())  # must not raise
    line = capsys.readouterr().err.strip()
    payload = json.loads(line)
    assert "unrenderable log record" in payload["message"]


def test_format_auto_picks_rich_on_a_tty(monkeypatch):
    monkeypatch.delenv("TEST_MCP_LOG_FORMAT", raising=False)
    # Monkeypatch the TTY probe itself, not sys.stderr: capsys/pytest already
    # substitute their own stderr object for the duration of the test, so
    # reassigning sys.stderr here would fight the test harness rather than
    # exercise the code path under test.
    monkeypatch.setattr(
        "markdown_vault_mcp._logging_setup._stderr_is_tty", lambda: True
    )
    configure_logging_from_env("TEST_MCP")
    owned = _owned_handlers()
    assert len(owned) == 2
    assert all(isinstance(h, RichHandler) for h in owned)


def test_format_auto_picks_json_on_a_non_tty(monkeypatch):
    monkeypatch.delenv("TEST_MCP_LOG_FORMAT", raising=False)
    monkeypatch.setattr(
        "markdown_vault_mcp._logging_setup._stderr_is_tty", lambda: False
    )
    configure_logging_from_env("TEST_MCP")
    owned = _owned_handlers()
    assert len(owned) == 1
    assert not isinstance(owned[0], RichHandler)


def test_format_unrecognised_value_falls_back_to_auto_silently(monkeypatch, caplog):
    # Consistent with an unknown LOG_LEVEL falling back to INFO: a typo in
    # the format name must not stop a server from starting, or warn either
    # — LOG_LEVEL's own fallback is silent, so this one matches it.
    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "bogus")
    monkeypatch.delenv("TEST_MCP_LOG_LEVEL", raising=False)
    monkeypatch.delenv("FASTMCP_LOG_LEVEL", raising=False)
    monkeypatch.setattr(
        "markdown_vault_mcp._logging_setup._stderr_is_tty", lambda: True
    )
    with caplog.at_level(logging.WARNING):
        configure_logging_from_env("TEST_MCP")
    assert caplog.records == []
    owned = _owned_handlers()
    assert len(owned) == 2
    assert all(isinstance(h, RichHandler) for h in owned)


def test_format_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "JSON")
    configure_logging_from_env("TEST_MCP")
    assert len(_owned_handlers()) == 1

    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "Rich")
    configure_logging_from_env("TEST_MCP")
    assert len(_owned_handlers()) == 2


def test_format_change_leaves_exactly_one_owned_chain(monkeypatch):
    # This is the case _OWNED_ATTR exists for: pvl-core must recognise and
    # remove its own handlers from a *previous* mode, not just a previous
    # call in the same mode.
    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "json")
    configure_logging_from_env("TEST_MCP")
    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "rich")
    configure_logging_from_env("TEST_MCP")
    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "json")
    configure_logging_from_env("TEST_MCP")
    owned = _owned_handlers()
    assert len(owned) == 1
    assert not isinstance(owned[0], RichHandler)


def test_format_json_exception_record_has_one_handler_and_an_exception_field(
    monkeypatch, capsys
):
    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "json")
    configure_logging_from_env("TEST_MCP")
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("some.domain.module").exception("failed")
    lines = [line for line in capsys.readouterr().err.strip().splitlines() if line]
    # No second handler double-printing the traceback: exactly one line.
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert "ValueError: boom" in payload["exception"]


def test_uvicorn_error_logger_untouched(monkeypatch):
    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "INFO")
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    configure_logging_from_env("TEST_MCP")
    assert logging.getLogger("uvicorn.error").level == logging.INFO


def test_demotion_idempotent_across_level_flips(monkeypatch):
    # uvicorn.access is left at NOTSET and no longer demoted (it gets the
    # failures-only filter instead — see test_access_filter_* below), so
    # this idempotency check exercises a logger that is still demoted.
    demoted = logging.getLogger("mcp.server.lowlevel.server")

    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "DEBUG")
    configure_logging_from_env("TEST_MCP")
    assert demoted.level == logging.NOTSET

    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "INFO")
    configure_logging_from_env("TEST_MCP")
    assert demoted.level == logging.WARNING

    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "DEBUG")
    configure_logging_from_env("TEST_MCP")
    assert demoted.level == logging.NOTSET


def test_debug_flood_logger_capped_at_info_at_debug(monkeypatch):
    # docket.worker's poll loop emits a DEBUG record per iteration on an
    # idle queue; capping it at INFO keeps DEBUG readable for first-party
    # diagnostics while leaving its lifecycle records in place.
    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "DEBUG")
    configure_logging_from_env("TEST_MCP")
    assert logging.getLogger("docket.worker").level == logging.INFO


def test_debug_flood_logger_capped_at_info_via_verbose(monkeypatch):
    monkeypatch.delenv("TEST_MCP_LOG_LEVEL", raising=False)
    configure_logging_from_env("TEST_MCP", verbose=True)
    assert logging.getLogger("docket.worker").level == logging.INFO


def test_debug_flood_logger_untouched_below_debug(monkeypatch):
    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "INFO")
    configure_logging_from_env("TEST_MCP")
    assert logging.getLogger("docket.worker").level == logging.NOTSET


def test_debug_flood_logger_untouched_above_info(monkeypatch):
    # The cap must never *raise* the effective level: at WARNING the logger
    # stays on inheritance so warnings and errors still pass through.
    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "WARNING")
    configure_logging_from_env("TEST_MCP")
    assert logging.getLogger("docket.worker").level == logging.NOTSET


def test_debug_flood_cap_idempotent_across_level_flips(monkeypatch):
    worker = logging.getLogger("docket.worker")

    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "DEBUG")
    configure_logging_from_env("TEST_MCP")
    assert worker.level == logging.INFO

    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "INFO")
    configure_logging_from_env("TEST_MCP")
    assert worker.level == logging.NOTSET

    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "DEBUG")
    configure_logging_from_env("TEST_MCP")
    assert worker.level == logging.INFO


def test_debug_flood_logger_filters_poll_records_at_debug(monkeypatch, caplog):
    # Behavioural check, not just a level assertion: at root DEBUG the poll
    # trace is dropped while the worker's INFO records survive.
    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "DEBUG")
    configure_logging_from_env("TEST_MCP")
    worker = logging.getLogger("docket.worker")

    # at_level() must target the root logger, not docket.worker: naming the
    # logger would reset the very cap under test.
    with caplog.at_level(logging.DEBUG):
        worker.debug("Getting new deliveries")
        worker.info("Starting worker")

    assert [record.getMessage() for record in caplog.records] == ["Starting worker"]


def test_debug_flood_cap_leaves_first_party_debug_intact(monkeypatch, caplog):
    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "DEBUG")
    configure_logging_from_env("TEST_MCP")

    with caplog.at_level(logging.DEBUG):
        logging.getLogger("markdown_vault_mcp.vault").debug("token_validated")

    assert [record.getMessage() for record in caplog.records] == ["token_validated"]


class _Capture(logging.Handler):
    """A non-console handler, standing in for an OTLP or file handler."""

    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def test_installs_one_handler_pair_at_root(monkeypatch):
    # Forces rich: this test is about the Rich-specific two-handler shape,
    # which auto-detection would not guarantee under pytest's own stderr
    # capture (not a TTY), so it would otherwise install the JSON handler
    # instead and the assertion below would be testing the wrong mode.
    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "rich")
    configure_logging_from_env("TEST_MCP")
    root = logging.getLogger()
    owned = [h for h in root.handlers if getattr(h, "_pvl_core_owned", False)]
    assert len(owned) == 2


def test_repeated_calls_do_not_stack_handlers():
    configure_logging_from_env("TEST_MCP")
    first = len(logging.getLogger().handlers)
    configure_logging_from_env("TEST_MCP")
    configure_logging_from_env("TEST_MCP")
    assert len(logging.getLogger().handlers) == first


def test_fastmcp_logger_is_neutralised():
    configure_logging_from_env("TEST_MCP")
    fastmcp_logger = logging.getLogger("fastmcp")
    assert fastmcp_logger.handlers == []
    assert fastmcp_logger.propagate is True
    assert fastmcp_logger.level == logging.NOTSET


def test_temporary_log_level_cannot_revert_the_topology():
    """The regression that parked #323: fastmcp re-running its own config."""
    from fastmcp.utilities.logging import configure_logging, temporary_log_level

    configure_logging_from_env("TEST_MCP")
    fastmcp_logger = logging.getLogger("fastmcp")
    with temporary_log_level("DEBUG"):
        assert fastmcp_logger.handlers == []
        assert fastmcp_logger.propagate is True
    configure_logging("INFO")
    assert fastmcp_logger.handlers == []
    assert fastmcp_logger.propagate is True


def test_fastmcp_debug_records_reach_root():
    configure_logging_from_env("TEST_MCP", verbose=True)
    capture = _Capture()
    logging.getLogger().addHandler(capture)
    logging.getLogger("fastmcp.middleware.requests").debug("probe")
    assert "probe" in capture.messages


def test_replaces_a_pre_existing_console_handler():
    root = logging.getLogger()
    console = logging.StreamHandler()  # defaults to sys.stderr
    root.addHandler(console)
    configure_logging_from_env("TEST_MCP")
    assert console not in root.handlers


def test_replaces_a_pre_existing_foreign_rich_handler():
    # Exercises the console.file fallback in _is_console_handler: a foreign
    # RichHandler() (not one of ours, so unmarked by _OWNED_ATTR) still
    # writes to a console stream via console.file, not .stream — the type
    # check alone would miss it and leave two Rich chains at root.
    root = logging.getLogger()
    foreign = RichHandler()  # defaults to Console() over sys.stdout
    root.addHandler(foreign)
    configure_logging_from_env("TEST_MCP")
    assert foreign not in root.handlers


def test_leaves_non_console_handlers_alone():
    """The #323 fix: an operator's OTLP handler at root must survive."""
    root = logging.getLogger()
    capture = _Capture()
    root.addHandler(capture)
    configure_logging_from_env("TEST_MCP")
    assert capture in root.handlers
    logging.getLogger("fastmcp.middleware.requests").warning("exported")
    logging.getLogger("some.domain.module").warning("also exported")
    assert capture.messages == ["exported", "also exported"]


def test_caplog_survives(caplog):
    """pytest's handler is a StreamHandler over StringIO — type-based
    console detection would detach it across the whole suite."""
    configure_logging_from_env("TEST_MCP")
    with caplog.at_level(logging.WARNING):
        logging.getLogger("some.domain.module").warning("captured")
    assert "captured" in caplog.text


def test_writes_nothing_to_stdout(capsys):
    # Must fail in both directions: stdout must stay empty, and the record
    # must actually reach stderr — a handler pair that silently dropped
    # every record would satisfy the stdout-only half of this check too.
    configure_logging_from_env("TEST_MCP")
    logging.getLogger("some.domain.module").warning("stderr only")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "stderr only" in captured.err


def test_exception_records_go_to_the_traceback_handler_only(monkeypatch):
    # Forces rich: this test is about the two-handler exc_info split, which
    # only exists in rich mode — JSON mode has one handler and no such
    # split (see the JSON exception-field test above).
    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "rich")
    configure_logging_from_env("TEST_MCP")
    root = logging.getLogger()
    owned = [h for h in root.handlers if getattr(h, "_pvl_core_owned", False)]
    plain = logging.LogRecord("x", logging.ERROR, "_", 0, "no traceback", None, None)
    try:
        raise ValueError("boom")
    except ValueError:
        import sys as _sys

        with_exc = logging.LogRecord(
            "x", logging.ERROR, "_", 0, "traceback", None, _sys.exc_info()
        )
    accepting_plain = [h for h in owned if h.filter(plain)]
    accepting_exc = [h for h in owned if h.filter(with_exc)]
    assert len(accepting_plain) == 1
    assert len(accepting_exc) == 1
    assert accepting_plain != accepting_exc


def _access_record(method: str, path: str, status: int) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="_",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("1.2.3.4:5678", method, path, "1.1", status),
        exc_info=None,
    )


def _access_filter(monkeypatch):
    # Mirrors the delenv sibling tests do: without it, a developer with
    # TEST_MCP_LOG_LEVEL=DEBUG exported gets no filter installed and every
    # `(log_filter,) = _access_filter(...)` unpack fails with a confusing
    # ValueError instead of a clear assertion.
    monkeypatch.delenv("TEST_MCP_LOG_LEVEL", raising=False)
    monkeypatch.delenv("FASTMCP_LOG_LEVEL", raising=False)
    configure_logging_from_env("TEST_MCP")
    access = logging.getLogger("uvicorn.access")
    return [f for f in access.filters if type(f).__name__ == "_AccessLogFilter"]


@pytest.mark.parametrize(
    ("status", "kept"),
    [
        (200, False),
        (204, False),
        (302, False),
        (400, True),
        (401, True),
        (404, True),
        (413, True),
        (500, True),
        (503, True),
    ],
)
def test_access_filter_keeps_only_failures(status, kept, monkeypatch):
    (log_filter,) = _access_filter(monkeypatch)
    assert log_filter.filter(_access_record("GET", "/mcp", status)) is kept


def test_access_filter_strips_the_query_string(monkeypatch):
    (log_filter,) = _access_filter(monkeypatch)
    record = _access_record("GET", "/authorize?code=SECRET&state=xyz", 401)
    assert log_filter.filter(record) is True
    assert "SECRET" not in record.getMessage()
    assert "/authorize" in record.getMessage()


def test_access_filter_redacts_the_transfer_token(monkeypatch):
    (log_filter,) = _access_filter(monkeypatch)
    record = _access_record("GET", "/transfer/tok_abc123", 404)
    assert log_filter.filter(record) is True
    assert "tok_abc123" not in record.getMessage()
    assert "transfer/<redacted>" in record.getMessage()


def test_access_filter_redacts_the_transfer_token_case_insensitively(monkeypatch):
    # Starlette routes case-sensitively, so "/TRANSFER/..." 404s — a status
    # this filter always keeps — and without a case-insensitive match the
    # token would reach the log unredacted precisely because the request
    # failed to route.
    (log_filter,) = _access_filter(monkeypatch)
    record = _access_record("GET", "/TRANSFER/tok_SECRET", 404)
    assert log_filter.filter(record) is True
    assert "tok_SECRET" not in record.getMessage()
    assert "transfer/<redacted>" in record.getMessage()


def test_access_filter_passes_records_of_another_shape(monkeypatch):
    (log_filter,) = _access_filter(monkeypatch)
    other = logging.LogRecord(
        "uvicorn.access", logging.INFO, "_", 0, "startup", None, None
    )
    assert log_filter.filter(other) is True


def test_access_filter_attaches_pvl_core_fields_when_it_keeps_a_record(monkeypatch):
    from markdown_vault_mcp._pvl_core import _ACCESS_FIELDS_ATTR

    (log_filter,) = _access_filter(monkeypatch)
    record = _access_record("GET", "/transfer/tok_abc123", 404)
    assert log_filter.filter(record) is True
    fields = getattr(record, _ACCESS_FIELDS_ATTR)
    assert fields.client == "1.2.3.4:5678"
    assert fields.method == "GET"
    # The attached path is the already-redacted one — the same value the
    # filter wrote back into record.args — never the raw token.
    assert fields.path == "/transfer/<redacted>"
    assert fields.status == 404
    assert isinstance(fields.status, int)


def test_access_filter_does_not_attach_fields_to_a_dropped_record(monkeypatch):
    from markdown_vault_mcp._pvl_core import _ACCESS_FIELDS_ATTR

    (log_filter,) = _access_filter(monkeypatch)
    record = _access_record("GET", "/mcp", 200)
    assert log_filter.filter(record) is False
    assert not hasattr(record, _ACCESS_FIELDS_ATTR)


def test_access_filter_does_not_attach_fields_to_an_unrecognised_record(monkeypatch):
    from markdown_vault_mcp._pvl_core import _ACCESS_FIELDS_ATTR

    (log_filter,) = _access_filter(monkeypatch)
    other = logging.LogRecord(
        "uvicorn.access", logging.INFO, "_", 0, "startup", None, None
    )
    assert log_filter.filter(other) is True
    assert not hasattr(other, _ACCESS_FIELDS_ATTR)


def test_end_to_end_json_access_line_carries_fields_not_message(monkeypatch, capsys):
    # Integration check, not unit-level: a real access.info(...) call must
    # pass through the installed filter and the installed JsonFormatter
    # together and come out with real fields — the filter and formatter
    # tests above each prove one half in isolation.
    monkeypatch.delenv("FASTMCP_LOG_LEVEL", raising=False)
    monkeypatch.setenv("TEST_MCP_LOG_FORMAT", "json")
    configure_logging_from_env("TEST_MCP")
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d',
        "1.2.3.4:5678",
        "GET",
        "/transfer/tok_abc123?code=SECRET",
        "1.1",
        404,
    )
    line = capsys.readouterr().err.strip()
    payload = json.loads(line)
    assert payload["client"] == "1.2.3.4:5678"
    assert payload["method"] == "GET"
    assert payload["path"] == "/transfer/<redacted>"
    assert payload["status"] == 404
    assert "message" not in payload
    assert "event" not in payload
    assert "tok_abc123" not in line
    assert "SECRET" not in line


def test_access_filter_present_and_keeps_success_at_debug():
    # Redaction must never be a side effect of verbosity: the filter stays
    # installed at DEBUG, it just stops dropping 2xx/3xx records.
    configure_logging_from_env("TEST_MCP", verbose=True)
    access = logging.getLogger("uvicorn.access")
    (log_filter,) = [
        f for f in access.filters if type(f).__name__ == "_AccessLogFilter"
    ]
    assert log_filter.filter(_access_record("GET", "/mcp", 200)) is True


def test_access_lines_redacted_at_debug():
    # Live handler attached at root, not the filter's return value: proves
    # the secrets are actually gone from what a handler receives, for the
    # two cases that only exist at DEBUG — a *live* transfer link (2xx,
    # only visible once successes stop being dropped) and an OAuth
    # authorize redirect.
    configure_logging_from_env("TEST_MCP", verbose=True)
    access = logging.getLogger("uvicorn.access")
    handler = logging.Handler()
    received: list[logging.LogRecord] = []
    handler.emit = received.append  # type: ignore[method-assign]
    logging.getLogger().addHandler(handler)
    try:
        access.info(
            '%s - "%s %s HTTP/%s" %d',
            "1.2.3.4:5678",
            "GET",
            "/transfer/tok_SECRET123",
            "1.1",
            200,
        )
        access.info(
            '%s - "%s %s HTTP/%s" %d',
            "1.2.3.4:5678",
            "GET",
            "/authorize?code=AUTHCODE&state=x",
            "1.1",
            302,
        )
        assert len(received) == 2
        messages = [r.getMessage() for r in received]
        assert not any("SECRET" in m or "AUTHCODE" in m for m in messages)
        assert "transfer/<redacted>" in messages[0]
        assert "/authorize" in messages[1] and "?" not in messages[1]
    finally:
        logging.getLogger().removeHandler(handler)


def test_access_filter_rule_tracks_the_latest_call(monkeypatch):
    # Idempotence must not go stale on a flip: the surviving instance after
    # DEBUG must apply DEBUG's rule (keep 200s), and after flipping back to
    # INFO it must apply INFO's rule (drop 200s) — never a rule left over
    # from before the flip.
    access = logging.getLogger("uvicorn.access")

    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "INFO")
    configure_logging_from_env("TEST_MCP")
    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "DEBUG")
    configure_logging_from_env("TEST_MCP")
    filters = [f for f in access.filters if type(f).__name__ == "_AccessLogFilter"]
    assert len(filters) == 1
    assert filters[0].filter(_access_record("GET", "/mcp", 200)) is True

    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "INFO")
    configure_logging_from_env("TEST_MCP")
    filters = [f for f in access.filters if type(f).__name__ == "_AccessLogFilter"]
    assert len(filters) == 1
    assert filters[0].filter(_access_record("GET", "/mcp", 200)) is False


def test_repeated_calls_leave_one_access_filter(monkeypatch):
    configure_logging_from_env("TEST_MCP")
    configure_logging_from_env("TEST_MCP")
    assert len(_access_filter(monkeypatch)) == 1


def test_debug_then_info_leaves_no_residue(monkeypatch):
    configure_logging_from_env("TEST_MCP", verbose=True)
    configure_logging_from_env("TEST_MCP")
    assert len(_access_filter(monkeypatch)) == 1


@pytest.mark.parametrize("name", ["mcp.server.lowlevel.server", "httpx", "httpcore"])
def test_noisy_loggers_demoted_at_info(name, monkeypatch):
    monkeypatch.delenv("TEST_MCP_LOG_LEVEL", raising=False)
    monkeypatch.delenv("FASTMCP_LOG_LEVEL", raising=False)
    configure_logging_from_env("TEST_MCP")
    assert logging.getLogger(name).level == logging.WARNING


@pytest.mark.parametrize("name", ["mcp.server.lowlevel.server", "httpx", "httpcore"])
def test_noisy_loggers_restored_at_debug(name):
    configure_logging_from_env("TEST_MCP", verbose=True)
    assert logging.getLogger(name).level == logging.NOTSET


@pytest.mark.parametrize("name", ["mcp.server.lowlevel.server", "httpx", "httpcore"])
def test_noisy_loggers_never_louder_than_operator_level(name, monkeypatch):
    # Regression: a flat WARNING demotion *raises* the effective level for
    # these loggers above ERROR, so they print WARNING records an operator
    # who chose ERROR explicitly asked not to see — louder than the
    # server's own first-party code, which correctly stays silent.
    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "ERROR")
    configure_logging_from_env("TEST_MCP")
    assert logging.getLogger(name).level == logging.ERROR


def test_uvicorn_error_is_never_demoted():
    configure_logging_from_env("TEST_MCP")
    assert logging.getLogger("uvicorn.error").level == logging.NOTSET


def test_access_logger_level_is_notset():
    """Left at NOTSET rather than pinned: the filter decides which requests
    are worth a line, the level decides whether the operator wants request
    lines at all, and the level answers its own question by inheritance."""
    configure_logging_from_env("TEST_MCP")
    assert logging.getLogger("uvicorn.access").level == logging.NOTSET


def test_docket_worker_capped_at_debug():
    configure_logging_from_env("TEST_MCP", verbose=True)
    assert logging.getLogger("docket.worker").level == logging.INFO


def test_access_line_dropped_by_level_before_the_filter_sees_it(monkeypatch):
    # End-to-end check with a real handler attached at root, not caplog, and
    # a real logging call (access.info(...)) rather than a hand-built record
    # pushed through .handle() — .handle() skips the isEnabledFor() level
    # check entirely, which is exactly the mechanism under test. uvicorn.access
    # is left at NOTSET so it inherits the root level: at WARNING that check
    # must drop even a kept-shape 404 before the failures-only filter ever
    # runs. The mirror at INFO confirms the same call does reach the handler.
    monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "WARNING")
    configure_logging_from_env("TEST_MCP")
    access = logging.getLogger("uvicorn.access")
    handler = logging.Handler()
    received: list[logging.LogRecord] = []
    handler.emit = received.append  # type: ignore[method-assign]
    logging.getLogger().addHandler(handler)
    try:
        access.info(
            '%s - "%s %s HTTP/%s" %d', "1.2.3.4:5678", "GET", "/mcp", "1.1", 404
        )
        assert received == []

        monkeypatch.setenv("TEST_MCP_LOG_LEVEL", "INFO")
        configure_logging_from_env("TEST_MCP")
        access.info(
            '%s - "%s %s HTTP/%s" %d', "1.2.3.4:5678", "GET", "/mcp", "1.1", 404
        )
        assert len(received) == 1
    finally:
        logging.getLogger().removeHandler(handler)


def test_filter_and_redaction_survive_a_direct_uvicorn_dictconfig():
    """Defence in depth for a downstream that calls uvicorn directly.

    ``run_http`` pins ``log_config=None``, so pvl-core's own path never
    triggers this. But nothing stops a downstream from bypassing
    ``run_http`` and calling ``uvicorn.Config(...).configure_logging()``
    itself — the way ``uvicorn.run(...)`` does by default — which
    reinstalls uvicorn's own handler on ``uvicorn.access`` (a plain
    ``StreamHandler`` on **stdout**, level ``INFO``, ``propagate=False``)
    regardless of ``{PREFIX}_LOG_LEVEL``. What survives even then is
    proven here: ``dictConfig`` replaces handlers, not filters, so
    ``_AccessLogFilter`` is still attached to ``uvicorn.access`` afterwards
    and still redacts whatever uvicorn logs through its own reinstalled
    handler.
    """
    configure_logging_from_env("TEST_MCP")
    uvicorn.Config(None, log_config=uvicorn.config.LOGGING_CONFIG).configure_logging()

    access = logging.getLogger("uvicorn.access")

    # The direct uvicorn.run(...) behaviour this simulates: uvicorn
    # reinstalled its own chain, not pvl-core's.
    assert access.propagate is False
    assert access.level == logging.INFO
    assert len(access.handlers) == 1
    assert getattr(access.handlers[0], "_pvl_core_owned", False) is False

    # What survives: our filter is still attached to the logger.
    filters = [f for f in access.filters if type(f).__name__ == "_AccessLogFilter"]
    assert len(filters) == 1

    # And it still redacts whatever reaches uvicorn's own handler.
    handler = access.handlers[0]
    original_stream = handler.stream
    buf = io.StringIO()
    handler.stream = buf
    try:
        access.info(
            '%s - "%s %s HTTP/%s" %d',
            "1.2.3.4:5678",
            "GET",
            "/transfer/tok_SECRET123",
            "1.1",
            404,
        )
    finally:
        handler.stream = original_stream
    line = buf.getvalue()
    assert "tok_SECRET123" not in line
    assert "transfer/<redacted>" in line
