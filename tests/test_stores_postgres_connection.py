"""The PostgreSQL connection layer: outcomes, conninfo, extension, schema, pool (#1504)."""

from __future__ import annotations

import logging
import socket
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from markdown_vault_mcp.exceptions import StoreNotConfiguredError, StoreUnavailableError
from markdown_vault_mcp.stores import _postgres as pg

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.fixtures.postgres import PostgresServer

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


def test_prepare_conninfo_adds_the_defaults() -> None:
    params = conninfo_to_dict(pg.prepare_conninfo("host=db dbname=x", schema=_SCHEMA))
    assert params["connect_timeout"] == "10"
    assert params["application_name"] == f"markdown-vault-mcp:{_SCHEMA}"
    assert (params["keepalives"], params["keepalives_idle"]) == ("1", "30")
    assert (params["keepalives_interval"], params["keepalives_count"]) == ("10", "3")


def test_prepare_conninfo_keeps_what_the_operator_set() -> None:
    dsn = "postgresql://u@db/x?connect_timeout=3&application_name=mine&keepalives=0"
    params = conninfo_to_dict(pg.prepare_conninfo(dsn, schema=_SCHEMA))
    assert (params["connect_timeout"], params["application_name"]) == ("3", "mine")
    assert params["keepalives"] == "0"


def test_an_invalid_dsn_is_not_configured_without_echoing_it() -> None:
    with pytest.raises(
        StoreNotConfiguredError, match="not a valid connection string"
    ) as failure:
        pg.prepare_conninfo("host=db password=TOPSECRET bogus_key=1", schema=_SCHEMA)
    assert "TOPSECRET" not in str(failure.value)


@pytest.fixture
def silent_port() -> Iterator[int]:
    """A TCP port that accepts connections and never answers the startup packet."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    listener.settimeout(0.2)
    held: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        while not stop.is_set():
            try:
                held.append(listener.accept()[0])
            except OSError:
                continue

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        stop.set()
        thread.join(timeout=2)
        for conn in held:
            conn.close()
        listener.close()


def _with(server: PostgresServer, **params: str) -> str:
    return make_conninfo(server.dsn, **params)


def _reason_of(conninfo: str) -> str:
    with pytest.raises(StoreUnavailableError) as failure:
        pg.connect(conninfo, schema=_SCHEMA)
    assert failure.value.__cause__ is None
    assert failure.value.__suppress_context__
    return str(failure.value).rsplit("(reason=", 1)[1].rstrip(")")


def test_connect_reports_a_wrong_password_as_refused(
    postgres_server: PostgresServer,
) -> None:
    assert _reason_of(_with(postgres_server, password="wrong")) == "refused"


def test_connect_reports_a_missing_database_as_refused(
    postgres_server: PostgresServer,
) -> None:
    assert _reason_of(_with(postgres_server, dbname="mvm_no_such_db")) == "refused"


def test_connect_reports_no_password_as_no_password(
    postgres_server: PostgresServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not postgres_server.throwaway:
        pytest.skip(
            "needs a server that demands a password (the harness's own container)"
        )
    monkeypatch.delenv("PGPASSWORD", raising=False)
    monkeypatch.setenv("PGPASSFILE", "/nonexistent")
    params = conninfo_to_dict(postgres_server.dsn)
    params.pop("password", None)
    assert _reason_of(make_conninfo(**params)) == "no_password"


def test_connect_reports_a_refused_port_as_connect(
    postgres_server: PostgresServer,
) -> None:
    assert _reason_of(_with(postgres_server, port="1")) == "connect"


def test_connect_reports_a_silent_peer_as_timeout(silent_port: int) -> None:
    conninfo = (
        f"host=127.0.0.1 port={silent_port} user=u password=p dbname=d "
        "connect_timeout=1"
    )
    assert _reason_of(conninfo) == "timeout"


def test_connect_returns_an_autocommit_connection(
    postgres_server: PostgresServer,
) -> None:
    conninfo = pg.prepare_conninfo(postgres_server.dsn, schema=_SCHEMA)
    conn = pg.connect(conninfo, schema=_SCHEMA)
    try:
        assert conn.autocommit
        app = conn.execute("SHOW application_name").fetchone()
        assert app == (f"markdown-vault-mcp:{_SCHEMA}",)
    finally:
        conn.close()
