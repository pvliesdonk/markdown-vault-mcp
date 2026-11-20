"""``maybe_start_debugpy`` (#2335).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_debug.py``.
"""

from __future__ import annotations

import logging
import sys
import types
from typing import Any

import pytest

from markdown_vault_mcp import _debug as debug_mod
from markdown_vault_mcp._debug import maybe_start_debugpy

# All tests use a stable per-app prefix so the env-var spelling is
# consistent.  Mirrors the pattern downstream consumers will use:
# every server picks an ``env_prefix`` and the debug helper reads
# ``{PREFIX}_DEBUG_PORT`` / ``{PREFIX}_DEBUG_WAIT`` from there.
# Underscore form (``MY_APP``) matches the README and the rest of the
# project's examples (``ServerConfig.from_env("MY_APP")`` etc.).
_PREFIX = "MY_APP"


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with the helper's idempotency latch reset."""
    monkeypatch.setattr(debug_mod, "_started", False)
    monkeypatch.delenv(f"{_PREFIX}_DEBUG_PORT", raising=False)
    monkeypatch.delenv(f"{_PREFIX}_DEBUG_WAIT", raising=False)


def _install_fake_debugpy(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """Inject a stub ``debugpy`` module recording listen / wait_for_client calls."""
    calls: list[tuple[str, Any]] = []

    def listen(addr: tuple[str, int]) -> tuple[str, int]:
        calls.append(("listen", addr))
        return addr

    def wait_for_client() -> None:
        calls.append(("wait", None))

    fake = types.ModuleType("debugpy")
    fake.listen = listen  # type: ignore[attr-defined]
    fake.wait_for_client = wait_for_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "debugpy", fake)
    return types.SimpleNamespace(calls=calls)


def test_no_op_when_debug_port_unset() -> None:
    # No env, no fake debugpy installed — must not raise.
    maybe_start_debugpy(_PREFIX)


@pytest.mark.parametrize("raw", ["0", "00", "+0", "-0", " 0 "])
def test_no_op_when_debug_port_parses_to_zero(
    raw: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # All forms that parse to 0 are the documented "disable" form and
    # must be silent — not the out-of-range WARNING that "70000" triggers.
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_PORT", raw)
    fake = _install_fake_debugpy(monkeypatch)

    with caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp"):
        maybe_start_debugpy(_PREFIX)

    assert fake.calls == []
    assert caplog.records == [], (
        f"{_PREFIX}_DEBUG_PORT={raw!r} should be a silent no-op, but logged: "
        f"{[r.getMessage() for r in caplog.records]}"
    )


def test_no_op_when_debug_port_blank(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_PORT", "   ")
    fake = _install_fake_debugpy(monkeypatch)

    with caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp"):
        maybe_start_debugpy(_PREFIX)

    assert fake.calls == []
    assert caplog.records == [], (
        f"blank {_PREFIX}_DEBUG_PORT must be a silent no-op, but logged: "
        f"{[r.getMessage() for r in caplog.records]}"
    )


def test_invalid_port_logs_warning_and_no_ops(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_PORT", "not-a-number")
    fake = _install_fake_debugpy(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp"):
        maybe_start_debugpy(_PREFIX)

    assert fake.calls == []
    assert any(
        f"{_PREFIX}_DEBUG_PORT" in rec.message and rec.levelno == logging.WARNING
        for rec in caplog.records
    )


def test_out_of_range_port_logs_warning_and_no_ops(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_PORT", "70000")
    fake = _install_fake_debugpy(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp"):
        maybe_start_debugpy(_PREFIX)

    assert fake.calls == []
    assert any(
        f"{_PREFIX}_DEBUG_PORT" in rec.message and rec.levelno == logging.WARNING
        for rec in caplog.records
    )


def test_negative_port_logs_warning_and_no_ops(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_PORT", "-1")
    fake = _install_fake_debugpy(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp"):
        maybe_start_debugpy(_PREFIX)

    assert fake.calls == []
    assert any(
        f"{_PREFIX}_DEBUG_PORT" in rec.message and rec.levelno == logging.WARNING
        for rec in caplog.records
    )


def test_debugpy_missing_logs_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_PORT", "5678")
    # Force ImportError when the helper tries to import debugpy.
    monkeypatch.setitem(sys.modules, "debugpy", None)

    # Pass the trailing-underscore prefix form so this also guards the
    # double-underscore normalisation of _debug.py's own ``port_var``: after
    # the env_int convergence, the import-failure message is the only remaining
    # consumer of ``port_var`` (the port-validation warning's var-name now
    # comes from env_int / _resolve_key, guarded separately by
    # test_trailing_underscore_prefix_normalised; ``wait_var`` builds its own
    # name for the DEBUG_WAIT message).
    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp"):
        maybe_start_debugpy(f"{_PREFIX}_")

    msgs = " ".join(rec.message for rec in caplog.records)
    assert "debugpy" in msgs
    # The hint should point at the install path so the operator can act on it.
    assert "extra" in msgs.lower() or "install" in msgs.lower()
    assert f"{_PREFIX}_DEBUG_PORT" in msgs and f"{_PREFIX}__DEBUG_PORT" not in msgs


def test_happy_path_calls_listen(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_PORT", "5678")
    fake = _install_fake_debugpy(monkeypatch)

    with caplog.at_level(logging.INFO, logger="markdown_vault_mcp"):
        maybe_start_debugpy(_PREFIX)

    assert fake.calls == [("listen", ("0.0.0.0", 5678))]  # noqa: S104, RUF100 - Asserted bind address; S104 is gate-only.
    assert any(
        rec.levelno == logging.INFO and "5678" in rec.message for rec in caplog.records
    )


def test_debug_wait_triggers_wait_for_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_PORT", "5678")
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_WAIT", "true")
    fake = _install_fake_debugpy(monkeypatch)

    maybe_start_debugpy(_PREFIX)

    assert ("listen", ("0.0.0.0", 5678)) in fake.calls  # noqa: S104, RUF100 - Asserted bind address; S104 is gate-only.
    assert ("wait", None) in fake.calls
    # listen must come before wait_for_client.
    assert fake.calls.index(("listen", ("0.0.0.0", 5678))) < fake.calls.index(  # noqa: S104, RUF100 - Asserted bind address; S104 is gate-only.
        ("wait", None)
    )


@pytest.mark.parametrize(
    "exc",
    [
        OSError("connection lost"),
        RuntimeError("debugpy internal error in wait"),
    ],
)
def test_wait_for_client_failure_logs_warning_and_continues(
    exc: Exception,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Symmetry with the listen() failure path: a wait_for_client() error
    # must not crash startup. The latch is correctly already True at this
    # point (listen succeeded), so subsequent calls short-circuit — but
    # the *current* call must continue, not raise.
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_PORT", "5678")
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_WAIT", "true")

    def listen(_: tuple[str, int]) -> None:
        return None

    def wait_boom() -> None:
        raise exc

    fake = types.ModuleType("debugpy")
    fake.listen = listen  # type: ignore[attr-defined]
    fake.wait_for_client = wait_boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "debugpy", fake)

    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp"):
        maybe_start_debugpy(_PREFIX)  # must not raise

    assert any(
        rec.levelno == logging.WARNING
        and "wait_for_client" in rec.message
        and str(exc) in rec.message
        for rec in caplog.records
    )
    # The listener bound successfully — the latch must stay set so a
    # re-caller correctly short-circuits. This is the contract the
    # wait-failure handling exists to preserve.
    assert debug_mod._started is True


def test_wait_for_client_keyboard_interrupt_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ctrl-C during wait must propagate so the operator can abort —
    # swallowing it would prevent process shutdown via SIGINT.
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_PORT", "5678")
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_WAIT", "true")

    def listen(_: tuple[str, int]) -> None:
        return None

    def wait_interrupt() -> None:
        raise KeyboardInterrupt

    fake = types.ModuleType("debugpy")
    fake.listen = listen  # type: ignore[attr-defined]
    fake.wait_for_client = wait_interrupt  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "debugpy", fake)

    with pytest.raises(KeyboardInterrupt):
        maybe_start_debugpy(_PREFIX)


def test_debug_wait_false_skips_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_PORT", "5678")
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_WAIT", "false")
    fake = _install_fake_debugpy(monkeypatch)

    maybe_start_debugpy(_PREFIX)

    assert ("listen", ("0.0.0.0", 5678)) in fake.calls  # noqa: S104, RUF100 - Asserted bind address; S104 is gate-only.
    assert ("wait", None) not in fake.calls


def test_idempotent_on_second_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_PORT", "5678")
    fake = _install_fake_debugpy(monkeypatch)

    maybe_start_debugpy(_PREFIX)
    maybe_start_debugpy(_PREFIX)

    # listen runs exactly once even though the helper was called twice.
    assert [c for c in fake.calls if c[0] == "listen"] == [
        ("listen", ("0.0.0.0", 5678))  # noqa: S104, RUF100 - Asserted bind address; S104 is gate-only.
    ]


@pytest.mark.parametrize(
    "exc",
    [
        OSError("address in use"),
        PermissionError("port requires CAP_NET_BIND_SERVICE"),
        # The broad except is documented as deliberate — non-OSError
        # failures (e.g. debugpy-internal RuntimeError on partial
        # installs) must also be absorbed rather than crashing startup.
        RuntimeError("debugpy internal error"),
    ],
)
def test_listen_failure_logs_warning_and_continues(
    exc: Exception,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_PORT", "5678")

    def boom(_: tuple[str, int]) -> None:
        raise exc

    fake = types.ModuleType("debugpy")
    fake.listen = boom  # type: ignore[attr-defined]
    fake.wait_for_client = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "debugpy", fake)

    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp"):
        maybe_start_debugpy(_PREFIX)  # must not raise

    assert any(
        rec.levelno == logging.WARNING and str(exc) in rec.message
        for rec in caplog.records
    )


def test_failed_listen_does_not_latch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If the first listen() fails (port in use, etc.) the idempotency
    # latch must NOT be set — a subsequent call (e.g. after the operator
    # frees the port and re-runs) is allowed to retry. Regression guard
    # for the "set _started after success" ordering in _debug.py.
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_PORT", "5678")
    attempts: list[tuple[str, int]] = []
    raise_once = {"left": True}

    def listen(addr: tuple[str, int]) -> None:
        attempts.append(addr)
        if raise_once["left"]:
            raise_once["left"] = False
            raise OSError("address in use")

    fake = types.ModuleType("debugpy")
    fake.listen = listen  # type: ignore[attr-defined]
    fake.wait_for_client = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "debugpy", fake)

    maybe_start_debugpy(_PREFIX)  # first attempt fails
    maybe_start_debugpy(_PREFIX)  # retry must reach listen() again

    assert attempts == [("0.0.0.0", 5678), ("0.0.0.0", 5678)]  # noqa: S104, RUF100 - Asserted bind address; S104 is gate-only.
    assert debug_mod._started is True  # latched only after the successful attempt


def test_trailing_underscore_prefix_normalised(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Caller may pass ``"MY_APP_"`` (with trailing underscore) — env
    # lookup AND log var-name must agree on the canonical
    # ``MY_APP_DEBUG_PORT`` form, not produce ``MY_APP__DEBUG_PORT``.
    # Regression guard against double-underscore drift: the DEBUG_PORT
    # warning's var-name now comes from ``env_int`` (via ``_resolve_key``'s
    # ``rstrip('_')``); ``_debug.py`` still builds ``port_var`` / ``wait_var``
    # with its own ``rstrip('_')`` for the other messages.
    monkeypatch.setenv(f"{_PREFIX}_DEBUG_PORT", "not-a-number")
    fake = _install_fake_debugpy(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp"):
        maybe_start_debugpy(f"{_PREFIX}_")

    assert fake.calls == []
    msgs = [rec.message for rec in caplog.records]
    assert any(
        f"{_PREFIX}_DEBUG_PORT" in m and f"{_PREFIX}__DEBUG_PORT" not in m for m in msgs
    ), f"Expected single underscore in log var-name, got: {msgs}"


def test_unprefixed_env_vars_no_longer_honored(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Regression guard for the 2.0.0-rc.2 breaking change: the legacy
    # unprefixed ``DEBUG_PORT`` / ``DEBUG_WAIT`` env vars are no longer
    # honored.  Setting them with no matching ``{PREFIX}_DEBUG_PORT``
    # must produce a silent no-op (no listener bound, no warning), so
    # an operator who has the legacy vars in their environment for
    # other tools doesn't accidentally fire up an unauthenticated
    # debugger on a server that hasn't opted in.
    monkeypatch.setenv("DEBUG_PORT", "5678")
    monkeypatch.setenv("DEBUG_WAIT", "true")
    fake = _install_fake_debugpy(monkeypatch)

    with caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp"):
        maybe_start_debugpy(_PREFIX)

    assert fake.calls == []
    assert caplog.records == [], (
        "Legacy unprefixed DEBUG_PORT must be a silent no-op, but logged: "
        f"{[r.getMessage() for r in caplog.records]}"
    )
