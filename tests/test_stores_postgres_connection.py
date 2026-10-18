"""The PostgreSQL connection layer: outcomes, conninfo, extension, schema, pool (#1504)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import psycopg
import pytest

from markdown_vault_mcp.exceptions import StoreNotConfiguredError, StoreUnavailableError
from markdown_vault_mcp.stores import _postgres as pg

_SCHEMA = "vault_a"


def _connect_error(*, needs: bool, used: bool) -> psycopg.OperationalError:
    """A connect failure as psycopg raises it, with a pgconn carrying the two flags."""
    fake = SimpleNamespace(needs_password=needs, used_password=used)
    return psycopg.OperationalError(
        "connection failed: FATAL: something",
        pgconn=fake,  # type: ignore[arg-type]  # carries only the two flags read
    )


def test_unavailable_names_the_reason_and_logs_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="markdown_vault_mcp.stores._postgres")
    exc = pg.unavailable("PostgreSQL refused.", reason="refused", schema=_SCHEMA)
    assert isinstance(exc, StoreUnavailableError)
    assert str(exc).endswith("(reason=refused)")
    assert (exc.backend, exc.location) == ("postgres", _SCHEMA)
    (record,) = caplog.records
    assert record.msg == "postgres_vector_store_unavailable schema=%s reason=%s"
    assert record.args == (_SCHEMA, "refused")


def test_not_configured_carries_backend_and_schema() -> None:
    exc = pg.not_configured("Create it.", schema=_SCHEMA)
    assert isinstance(exc, StoreNotConfiguredError)
    assert (exc.backend, exc.location) == ("postgres", _SCHEMA)


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (psycopg.errors.ConnectionTimeout("connection timeout expired"), "timeout"),
        (psycopg.OperationalError("could not translate host name"), "connect"),
        (_connect_error(needs=True, used=True), "no_password"),
        (_connect_error(needs=False, used=True), "refused"),
        (_connect_error(needs=False, used=False), "connect"),
    ],
)
def test_connect_failures_get_the_documented_reason(
    exc: psycopg.OperationalError, reason: str
) -> None:
    outcome = pg.classify_connect_failure(exc, schema=_SCHEMA)
    assert isinstance(outcome, StoreUnavailableError)
    assert str(outcome).endswith(f"(reason={reason})")


def test_needs_password_is_tested_before_used_password() -> None:
    """libpq sets used_password when none was supplied too (reference: libpq-status)."""
    outcome = pg.classify_connect_failure(
        _connect_error(needs=True, used=True), schema=_SCHEMA
    )
    assert "reason=no_password" in str(outcome)


@pytest.mark.parametrize(
    ("sqlstate", "reason"),
    [
        ("08006", "connect"),
        ("57P01", "connect"),
        ("53300", "connect"),
        ("28P01", "refused"),
        ("42501", "privilege"),
        ("22P02", "error"),
        ("XX000", "error"),
    ],
)
def test_errors_after_connecting_are_unavailable_by_class(
    sqlstate: str, reason: str
) -> None:
    exc = psycopg.errors.lookup(sqlstate)("boom")
    outcome = pg.classify_error(exc, schema=_SCHEMA, action="opened")
    assert isinstance(outcome, StoreUnavailableError)
    assert str(outcome).endswith(f"(reason={reason})")
    if reason == "error":
        assert sqlstate in str(outcome)


def test_error_without_sqlstate_is_unavailable_connect() -> None:
    outcome = pg.classify_error(
        psycopg.OperationalError("server closed the connection unexpectedly"),
        schema=_SCHEMA,
        action="read",
    )
    assert isinstance(outcome, StoreUnavailableError)
    assert str(outcome).endswith("(reason=connect)")
