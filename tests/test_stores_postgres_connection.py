"""The PostgreSQL connection layer: outcomes, conninfo, extension, schema, pool (#1504)."""

from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from pgvector import Vector
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from markdown_vault_mcp.exceptions import (
    StoreBusyError,
    StoreNotConfiguredError,
    StoreUnavailableError,
)
from markdown_vault_mcp.stores import _postgres as pg
from markdown_vault_mcp.stores.postgres_vectors import PostgresVectorBackend
from markdown_vault_mcp.types import EmbeddingIdentity
from tests.fixtures.postgres import OLD_PGVECTOR_ENV

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.fixtures.postgres import PostgresSchema, PostgresServer

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
    assert (
        record.msg == "postgres_vector_store_unavailable schema=%s reason=%s detail=%s"
    )
    assert record.args == (_SCHEMA, "refused", "PostgreSQL refused.")


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
        ("57014", "timeout"),
        ("55P03", "timeout"),
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


def _with(server: PostgresServer, **params: str) -> pg.Conninfo:
    return pg.prepare_conninfo(make_conninfo(server.dsn, **params), schema=_SCHEMA)


def _reason_of(conninfo: pg.Conninfo) -> str:
    with pytest.raises(StoreUnavailableError) as failure:
        pg.connect(conninfo, schema=_SCHEMA)
    assert failure.value.__cause__ is None
    assert failure.value.__suppress_context__
    return str(failure.value).rsplit("(reason=", 1)[1].rstrip(")")


def test_connect_reports_a_wrong_password_as_refused(
    postgres_server: PostgresServer,
) -> None:
    if not postgres_server.throwaway:
        pytest.skip(
            "needs a server that checks passwords (the harness's own container)"
        )
    assert _reason_of(_with(postgres_server, password="wrong")) == "refused"


def test_connect_reports_a_missing_database_as_refused(
    postgres_server: PostgresServer,
) -> None:
    if not postgres_server.throwaway:
        pytest.skip(
            "needs a server that checks passwords (the harness's own container)"
        )
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
    conninfo = pg.prepare_conninfo(make_conninfo("", **params), schema=_SCHEMA)
    assert _reason_of(conninfo) == "no_password"


def test_connect_reports_a_refused_port_as_connect(
    postgres_server: PostgresServer,
) -> None:
    assert _reason_of(_with(postgres_server, port="1")) == "connect"


def test_connect_reports_a_silent_peer_as_timeout(silent_port: int) -> None:
    conninfo = pg.prepare_conninfo(
        f"host=127.0.0.1 port={silent_port} user=u password=p dbname=d "
        "connect_timeout=1",
        schema=_SCHEMA,
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


def test_no_failure_carries_the_password(
    postgres_server: PostgresServer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    secret = "LEAK-CANARY-1504"
    attempts = [
        lambda: pg.prepare_conninfo(
            f"host=db password={secret} bogus_key=1", schema=_SCHEMA
        ),
        lambda: pg.connect(_with(postgres_server, password=secret), schema=_SCHEMA),
        lambda: pg.connect(
            _with(postgres_server, password=secret, port="1"), schema=_SCHEMA
        ),
        lambda: pg.connect(
            _with(postgres_server, password=secret, dbname="mvm_no_such_db"),
            schema=_SCHEMA,
        ),
    ]
    failures: list[BaseException] = []
    for attempt in attempts:
        with pytest.raises((StoreNotConfiguredError, StoreUnavailableError)) as failure:
            attempt()
        failures.append(failure.value)
    for exc in failures:
        assert secret not in str(exc)
        assert secret not in repr(exc)
        assert getattr(exc, "location", "") == _SCHEMA
    assert secret not in caplog.text


def test_find_extension_reports_its_schema_and_version(
    postgres_server: PostgresServer,
) -> None:
    with pg.connect(
        pg.prepare_conninfo(postgres_server.dsn, schema=_SCHEMA), schema=_SCHEMA
    ) as conn:
        ext = pg.find_extension(conn, schema=_SCHEMA)
    with psycopg.connect(postgres_server.dsn) as admin:
        row = admin.execute(
            "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n"
            " ON n.oid = e.extnamespace WHERE e.extname = 'vector'"
        ).fetchone()
    assert row is not None
    assert ext.schema == row[0]
    assert ext.version >= pg.MIN_PGVECTOR


def test_an_extension_older_than_the_minimum_is_not_configured(
    postgres_server: PostgresServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pg, "MIN_PGVECTOR", (99, 0, 0))
    with (
        pg.connect(
            pg.prepare_conninfo(postgres_server.dsn, schema=_SCHEMA), schema=_SCHEMA
        ) as conn,
        pytest.raises(StoreNotConfiguredError, match=r"99\.0\.0") as failure,
    ):
        pg.find_extension(conn, schema=_SCHEMA)
    assert "installed" in str(failure.value)


def test_a_real_pgvector_below_the_minimum_is_refused_at_open(
    postgres_schema: PostgresSchema,
) -> None:
    if os.environ.get(OLD_PGVECTOR_ENV, "").strip().lower() != "true":
        pytest.skip(f"needs a pgvector below the minimum and {OLD_PGVECTOR_ENV}=true")
    with psycopg.connect(postgres_schema.dsn) as admin:
        row = admin.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
    assert row is not None
    installed = tuple(int(part) for part in str(row[0]).split("."))
    assert installed < pg.MIN_PGVECTOR
    backend = PostgresVectorBackend(postgres_schema.dsn)
    with pytest.raises(StoreNotConfiguredError) as failure:
        backend.open(postgres_schema.name, EmbeddingIdentity("fake", "m", "", "v1", 3))
    message = str(failure.value)
    assert f"is {row[0]} installed" in message
    assert "needs 0.7.0 or later" in message
    assert "ALTER EXTENSION vector UPDATE;" in message


def _admin_execute(server: PostgresServer, statement: sql.Composable) -> None:
    with psycopg.connect(server.dsn, autocommit=True) as admin:
        admin.execute(statement)


def test_a_database_without_the_extension_is_not_configured(
    postgres_server: PostgresServer,
) -> None:
    if not postgres_server.throwaway:
        pytest.skip("creates a database: runs only against the harness's own container")
    database = f"mvm_test_noext_{uuid.uuid4().hex[:8]}"
    try:
        _admin_execute(
            postgres_server,
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                sql.Identifier(database)
            ),
        )
        conn = pg.connect(_with(postgres_server, dbname=database), schema=_SCHEMA)
        try:
            with pytest.raises(
                StoreNotConfiguredError, match="CREATE EXTENSION vector"
            ):
                pg.find_extension(conn, schema=_SCHEMA)
        finally:
            conn.close()
    finally:
        _admin_execute(
            postgres_server,
            sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)),
        )


def test_a_missing_schema_is_not_configured_with_the_sql(
    postgres_server: PostgresServer,
) -> None:
    with (
        pg.connect(
            pg.prepare_conninfo(postgres_server.dsn, schema="mvm_no_such_schema"),
            schema="mvm_no_such_schema",
        ) as conn,
        pytest.raises(StoreNotConfiguredError, match="CREATE SCHEMA") as failure,
    ):
        pg.check_schema(conn, schema="mvm_no_such_schema")
    assert "AUTHORIZATION" in str(failure.value)


def test_an_existing_schema_passes(postgres_schema: PostgresSchema) -> None:
    with pg.connect(
        pg.prepare_conninfo(postgres_schema.dsn, schema=postgres_schema.name),
        schema=postgres_schema.name,
    ) as conn:
        pg.check_schema(conn, schema=postgres_schema.name)


def test_schema_without_usage_is_unavailable_privilege(
    postgres_server: PostgresServer,
) -> None:
    if not postgres_server.throwaway:
        pytest.skip("creates a role: runs only against the harness's own container")
    role = f"mvm_test_role_{uuid.uuid4().hex[:8]}"
    schema = f"mvm_test_{uuid.uuid4().hex[:12]}"
    try:
        _admin_execute(
            postgres_server,
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'pw'").format(sql.Identifier(role)),
        )
        _admin_execute(
            postgres_server, sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema))
        )
        conn = pg.connect(
            _with(postgres_server, user=role, password="pw"), schema=schema
        )
        try:
            with pytest.raises(StoreUnavailableError, match=r"reason=privilege"):
                pg.check_schema(conn, schema=schema)
        finally:
            conn.close()
    finally:
        _admin_execute(
            postgres_server,
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)),
        )
        _admin_execute(
            postgres_server,
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)),
        )


def _extension_for(conninfo: pg.Conninfo, schema: str) -> pg.Extension:
    with pg.connect(conninfo, schema=schema) as conn:
        pg.check_schema(conn, schema=schema)
        return pg.find_extension(conn, schema=schema)


def test_pool_connections_are_configured(postgres_schema: PostgresSchema) -> None:
    name = postgres_schema.name
    conninfo = pg.prepare_conninfo(postgres_schema.dsn, schema=name)
    ext = _extension_for(conninfo, name)
    pool = pg.open_pool(
        conninfo,
        schema=name,
        extension=ext,
        settings=pg.PoolSettings(pool_size=2, statement_timeout_s=7),
    )
    try:
        with pool.connection() as conn:
            assert conn.autocommit
            path = conn.execute("SELECT current_schemas(false)").fetchone()
            assert path == ([name, ext.schema],)
            assert conn.execute("SHOW statement_timeout").fetchone() == ("7s",)
            vec = conn.execute("SELECT '[1,2]'::vector").fetchone()
            assert vec is not None
            assert isinstance(vec[0], Vector)
        assert pool.max_size == 2
    finally:
        pool.close()


def test_pool_sets_search_path_for_a_mixed_case_schema(
    postgres_server: PostgresServer,
) -> None:
    name = f"mvm_test_Mixed-{uuid.uuid4().hex[:6]}"
    _admin_execute(
        postgres_server, sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name))
    )
    try:
        conninfo = pg.prepare_conninfo(postgres_server.dsn, schema=name)
        ext = _extension_for(conninfo, name)
        pool = pg.open_pool(
            conninfo,
            schema=name,
            extension=ext,
            settings=pg.PoolSettings(pool_size=1),
        )
        try:
            with pool.connection() as conn:
                path = conn.execute("SELECT current_schemas(false)").fetchone()
            assert path == ([name, ext.schema],)
        finally:
            pool.close()
    finally:
        _admin_execute(
            postgres_server,
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)),
        )


def test_pool_that_cannot_connect_is_unavailable_pool_and_closed(
    silent_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[pg.ConnectionPool[Any]] = []

    class Recording(pg.ConnectionPool):  # type: ignore[type-arg]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            opened.append(self)

    monkeypatch.setattr(pg, "ConnectionPool", Recording)
    conninfo = pg.prepare_conninfo(
        f"host=127.0.0.1 port={silent_port} user=u password=p dbname=d "
        "connect_timeout=1",
        schema=_SCHEMA,
    )
    extension = pg.Extension("public", (0, 8, 0))
    settings = pg.PoolSettings(pool_size=1, wait_s=2)
    with pytest.raises(StoreUnavailableError, match=r"reason=pool"):
        pg.open_pool(conninfo, schema=_SCHEMA, extension=extension, settings=settings)
    (pool,) = opened
    assert pool.closed
    assert not [t for t in threading.enumerate() if t.name.startswith("mvm-vectors-")]


def test_a_pool_size_below_one_is_refused() -> None:
    conninfo = pg.prepare_conninfo("host=x", schema=_SCHEMA)
    extension = pg.Extension("public", (0, 8, 0))
    settings = pg.PoolSettings(pool_size=0)
    with pytest.raises(ValueError, match="pool_size"):
        pg.open_pool(conninfo, schema=_SCHEMA, extension=extension, settings=settings)


@pytest.mark.parametrize("check", ["find_extension", "check_schema"])
def test_a_connection_lost_before_the_catalogue_read_is_unavailable_connect(
    postgres_server: PostgresServer, check: str
) -> None:
    """A dropped connection is an outage, never a missing extension or schema."""
    conn = pg.connect(
        pg.prepare_conninfo(postgres_server.dsn, schema=_SCHEMA), schema=_SCHEMA
    )
    conn.close()
    read_catalogue = getattr(pg, check)
    with pytest.raises(StoreUnavailableError, match=r"reason=connect") as failure:
        read_catalogue(conn, schema=_SCHEMA)
    assert failure.value.__cause__ is None


@pytest.mark.parametrize(
    "dsn",
    [
        "host=db user=u password=TOP SECRET dbname=x",
        "postgresql://u:TOP%ZZSECRET@db/x",
        "postgresql://u:TOPSECRET@[db/x",
        "host=db password=TOPSECRET bogus_key=1",
    ],
)
def test_an_unparsable_dsn_carries_no_libpq_text(dsn: str) -> None:
    with pytest.raises(StoreNotConfiguredError, match="percent-encoding") as failure:
        pg.prepare_conninfo(dsn, schema=_SCHEMA)
    assert "SECRET" not in str(failure.value)
    assert failure.value.__cause__ is None


@pytest.mark.parametrize(
    "dsn",
    ["postgresql://u:S@CRET@127.0.0.1/x", "host='db CRET' dbname=x"],
)
def test_a_host_that_holds_a_password_fragment_is_refused(dsn: str) -> None:
    with pytest.raises(StoreNotConfiguredError, match="%40") as failure:
        pg.prepare_conninfo(dsn, schema=_SCHEMA)
    assert "CRET" not in str(failure.value)


def test_empty_settings_take_the_defaults() -> None:
    params = conninfo_to_dict(
        pg.prepare_conninfo(
            "host=db connect_timeout='' application_name=''", schema=_SCHEMA
        )
    )
    assert params["connect_timeout"] == "10"
    assert params["application_name"] == f"markdown-vault-mcp:{_SCHEMA}"


def test_libpq_environment_settings_are_respected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PGCONNECT_TIMEOUT", "4")
    monkeypatch.setenv("PGAPPNAME", "from-env")
    params = conninfo_to_dict(pg.prepare_conninfo("host=db", schema=_SCHEMA))
    assert "connect_timeout" not in params
    assert "application_name" not in params


def test_a_malformed_setting_is_not_configured_not_a_raw_error() -> None:
    conninfo = pg.prepare_conninfo("host=127.0.0.1 connect_timeout=abc", schema=_SCHEMA)
    with pytest.raises(StoreNotConfiguredError, match="connect_timeout") as failure:
        pg.connect(conninfo, schema=_SCHEMA)
    assert failure.value.__cause__ is None


def test_conninfo_hides_itself() -> None:
    conninfo = pg.prepare_conninfo("host=db password=TOPSECRET", schema=_SCHEMA)
    assert "TOPSECRET" not in repr(conninfo)
    assert "TOPSECRET" in conninfo  # still the real string for psycopg


def test_redact_keeps_safe_tokens_and_hides_the_rest() -> None:
    text = (
        'connection to server at "127.0.0.1", port 1 failed: FATAL: database "x" '
        "does not exist; failed to resolve host 'CRET@db'; bad value for option "
        '"connect_timeout"'
    )
    redacted = pg.redact(text, safe={"127.0.0.1", "x"})
    assert '"127.0.0.1"' in redacted
    assert 'database "x"' in redacted
    assert '"connect_timeout"' in redacted
    assert "CRET" not in redacted
    assert "<redacted>" in redacted


def test_connect_failure_messages_keep_the_diagnosis(
    postgres_server: PostgresServer,
) -> None:
    conninfo = _with(postgres_server, dbname="mvm_no_such_db")
    with pytest.raises(StoreUnavailableError) as failure:
        pg.connect(conninfo, schema=_SCHEMA)
    assert '"mvm_no_such_db"' in str(failure.value)
    assert not str(failure.value).endswith("\n")


@pytest.mark.parametrize(
    ("settings", "match"),
    [
        (pg.PoolSettings(statement_timeout_s=-1), "statement_timeout_s"),
        (pg.PoolSettings(statement_timeout_s=float("inf")), "statement_timeout_s"),
        (pg.PoolSettings(statement_timeout_s=float("nan")), "statement_timeout_s"),
        (pg.PoolSettings(wait_s=0), "wait_s"),
        (pg.PoolSettings(get_timeout_s=-1), "get_timeout_s"),
    ],
)
def test_pool_settings_out_of_range_are_refused(
    settings: pg.PoolSettings, match: str
) -> None:
    conninfo = pg.prepare_conninfo("host=x", schema=_SCHEMA)
    extension = pg.Extension("public", (0, 8, 0))
    with pytest.raises(ValueError, match=match):
        pg.open_pool(conninfo, schema=_SCHEMA, extension=extension, settings=settings)


@pytest.mark.parametrize(
    ("seconds", "milliseconds"), [(0, 0), (0.0004, 1), (0.0015, 2), (30, 30_000)]
)
def test_statement_timeout_rounds_to_milliseconds_without_reaching_none(
    seconds: float, milliseconds: int
) -> None:
    assert pg.statement_timeout_ms(pg.PoolSettings(statement_timeout_s=seconds)) == (
        milliseconds
    )


def test_a_statement_timeout_above_postgresqls_maximum_is_refused() -> None:
    too_long = pg.PoolSettings(statement_timeout_s=1e9)
    with pytest.raises(ValueError, match="statement_timeout_s"):
        pg.statement_timeout_ms(too_long)
    assert pg.statement_timeout_ms(
        pg.PoolSettings(statement_timeout_s=2147483.647)
    ) == (2147483647)


def test_an_extension_schema_without_usage_is_unavailable_privilege(
    postgres_server: PostgresServer,
) -> None:
    """pgvector installed in a schema the role cannot use (#1504 8d's review).

    Without the check, ``register_vector`` fails in every pool connection's
    setup and the store reports ``reason=pool`` after the pool's wait.
    """
    if not postgres_server.throwaway:
        pytest.skip("creates a database: runs only against the harness's own container")
    database = f"mvm_test_extns_{uuid.uuid4().hex[:8]}"
    role = f"mvm_test_role_{uuid.uuid4().hex[:8]}"
    try:
        _admin_execute(
            postgres_server,
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                sql.Identifier(database)
            ),
        )
        _admin_execute(
            postgres_server,
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'pw'").format(sql.Identifier(role)),
        )
        with psycopg.connect(
            make_conninfo(postgres_server.dsn, dbname=database), autocommit=True
        ) as admin:
            admin.execute("CREATE SCHEMA ext")
            admin.execute("CREATE EXTENSION vector SCHEMA ext")  # no grant on ext
        conn = pg.connect(
            _with(postgres_server, dbname=database, user=role, password="pw"),
            schema=_SCHEMA,
        )
        try:
            with pytest.raises(
                StoreUnavailableError,
                match=r'GRANT USAGE ON SCHEMA "ext" TO .*reason=privilege',
            ):
                pg.find_extension(conn, schema=_SCHEMA)
        finally:
            conn.close()
    finally:
        _admin_execute(
            postgres_server,
            sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)),
        )
        _admin_execute(
            postgres_server,
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)),
        )


def test_only_a_held_lease_is_busy() -> None:
    held = pg.unavailable("Taken.", reason="lease", schema=_SCHEMA)
    refused = pg.unavailable("PostgreSQL refused.", reason="refused", schema=_SCHEMA)
    assert isinstance(held, StoreBusyError)  # stop the server first (#1505)
    assert isinstance(refused, StoreUnavailableError)
    assert not isinstance(refused, StoreBusyError)
