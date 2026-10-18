"""Opening a PostgreSQL schema as a vector store (#1504)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import uuid
from typing import TYPE_CHECKING

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import PoolTimeout

from markdown_vault_mcp.exceptions import (
    ReadOnlyError,
    StoreCorruptError,
    StoreIncompatibleError,
    StoreNotConfiguredError,
    StoreUnavailableError,
    StoreUnsupportedSchemaError,
)
from markdown_vault_mcp.stores import _postgres as pg
from markdown_vault_mcp.stores import _postgres_tables as tables
from markdown_vault_mcp.stores._postgres_lease import LEASE_CLASS
from markdown_vault_mcp.stores.postgres_vectors import PostgresVectorBackend
from markdown_vault_mcp.stores.registry import get_backend
from markdown_vault_mcp.types import EmbeddingIdentity

if TYPE_CHECKING:
    from tests.fixtures.postgres import PostgresSchema, PostgresServer

_ID = EmbeddingIdentity("ollama", "nomic", "", "v1", 3)
_UNKNOWN_DIM = EmbeddingIdentity("ollama", "nomic", "", "v1", 0)


def _plant(
    schema: PostgresSchema, *, identity: EmbeddingIdentity = _ID, generation: int = 0
) -> None:
    conninfo = pg.prepare_conninfo(schema.dsn, schema=schema.name)
    with pg.connect(conninfo, schema=schema.name) as conn, conn.transaction():
        ext = pg.find_extension(conn, schema=schema.name)
        tables.create_tables(conn, schema=schema.name, extension=ext, identity=identity)
        conn.execute(
            sql.SQL("UPDATE {}.vector_meta SET generation = %s").format(
                sql.Identifier(schema.name)
            ),
            (generation,),
        )


def test_the_backend_is_registered() -> None:
    assert get_backend("vector", "postgres") is PostgresVectorBackend


def test_no_location_is_not_configured() -> None:
    backend = PostgresVectorBackend("host=db")
    with pytest.raises(StoreNotConfiguredError, match="schema"):
        backend.open(None, _ID)


def test_an_identity_is_required() -> None:
    backend = PostgresVectorBackend("host=db")
    with pytest.raises(ValueError, match="EmbeddingIdentity"):
        backend.open("vault_a", object())


def test_an_empty_schema_opens_as_an_empty_store(
    postgres_schema: PostgresSchema,
) -> None:
    store = PostgresVectorBackend(postgres_schema.dsn).open(postgres_schema.name, _ID)
    try:
        assert store.identity == _ID
        assert store.generation == 0
        assert (store.schema, store.read_only) == (postgres_schema.name, False)
    finally:
        store.close()


def test_a_planted_store_serves_its_identity_and_generation(
    postgres_schema: PostgresSchema,
) -> None:
    _plant(postgres_schema, generation=7)
    store = PostgresVectorBackend(postgres_schema.dsn).open(
        postgres_schema.name, _UNKNOWN_DIM, owns_files=False
    )
    try:
        assert store.identity == _ID  # wanted dimension 0 takes the stored 3
        assert store.generation == 7
        assert store.read_only
    finally:
        store.close()


@pytest.mark.parametrize(
    "wanted",
    [
        EmbeddingIdentity("ollama", "other", "", "v1", 3),
        EmbeddingIdentity("ollama", "nomic", "", "v1", 4),
    ],
)
def test_another_identity_is_incompatible(
    postgres_schema: PostgresSchema, wanted: EmbeddingIdentity
) -> None:
    _plant(postgres_schema)
    backend = PostgresVectorBackend(postgres_schema.dsn)
    with pytest.raises(StoreIncompatibleError):
        backend.open(postgres_schema.name, wanted)


def test_a_newer_format_is_unsupported(postgres_schema: PostgresSchema) -> None:
    _plant(postgres_schema)
    with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("UPDATE {}.vector_meta SET format_version = %s").format(
                sql.Identifier(postgres_schema.name)
            ),
            (pg.FORMAT_VERSION + 1,),
        )
    backend = PostgresVectorBackend(postgres_schema.dsn)
    with pytest.raises(StoreUnsupportedSchemaError):
        backend.open(postgres_schema.name, _ID)


def test_partial_tables_are_corrupt(postgres_schema: PostgresSchema) -> None:
    _plant(postgres_schema)
    with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP TABLE {}.vector_chunks").format(
                sql.Identifier(postgres_schema.name)
            )
        )
    backend = PostgresVectorBackend(postgres_schema.dsn)
    with pytest.raises(StoreCorruptError):
        backend.open(postgres_schema.name, _ID)


def test_a_missing_schema_is_not_configured(postgres_server: PostgresServer) -> None:
    backend = PostgresVectorBackend(postgres_server.dsn)
    with pytest.raises(StoreNotConfiguredError, match="CREATE SCHEMA"):
        backend.open("mvm_test_no_such_schema", _ID)


def test_an_unreachable_server_is_unavailable_never_corrupt() -> None:
    backend = PostgresVectorBackend(
        "host=127.0.0.1 port=1 user=u password=LEAK-1504 dbname=d connect_timeout=2"
    )
    with pytest.raises(StoreUnavailableError, match="reason=connect") as failure:
        backend.open("vault_a", _ID)
    assert "LEAK-1504" not in str(failure.value)


def test_an_unavailable_layout_read_propagates_from_open(
    postgres_schema: PostgresSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.stores.postgres_vectors as module

    def dropped(*_args: object, **_kwargs: object) -> object:
        raise pg.classify_error(
            psycopg.OperationalError("server closed the connection unexpectedly"),
            schema=postgres_schema.name,
            action="opened",
        )

    monkeypatch.setattr(module, "check_layout", dropped)
    backend = PostgresVectorBackend(postgres_schema.dsn)
    with pytest.raises(StoreUnavailableError, match="reason=connect"):
        backend.open(postgres_schema.name, _ID)


def test_tables_the_role_cannot_read_are_unavailable_privilege(
    postgres_schema: PostgresSchema, postgres_server: PostgresServer
) -> None:
    if not postgres_server.throwaway:
        pytest.skip("creates a role: runs only against the harness's own container")
    _plant(postgres_schema)
    role = f"mvm_test_role_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(postgres_server.dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'pw'").format(sql.Identifier(role))
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(postgres_schema.name), sql.Identifier(role)
            )
        )
    try:
        dsn = make_conninfo(postgres_server.dsn, user=role, password="pw")
        backend = PostgresVectorBackend(dsn)
        with pytest.raises(StoreUnavailableError, match="reason=privilege"):
            backend.open(postgres_schema.name, _ID, owns_files=False)
    finally:
        with psycopg.connect(postgres_server.dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_a_writer_without_create_on_an_empty_schema_is_refused(
    postgres_schema: PostgresSchema, postgres_server: PostgresServer
) -> None:
    if not postgres_server.throwaway:
        pytest.skip("creates a role: runs only against the harness's own container")
    role = f"mvm_test_role_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(postgres_server.dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'pw'").format(sql.Identifier(role))
        )
        admin.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(postgres_schema.name), sql.Identifier(role)
            )
        )
    try:
        dsn = make_conninfo(postgres_server.dsn, user=role, password="pw")
        backend = PostgresVectorBackend(dsn)
        reader = backend.open(postgres_schema.name, _ID, owns_files=False)
        reader.close()
        with pytest.raises(StoreUnavailableError, match="reason=privilege"):
            backend.open(postgres_schema.name, _ID, owns_files=True)
    finally:
        with psycopg.connect(postgres_server.dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_the_generation_is_read_live(postgres_schema: PostgresSchema) -> None:
    _plant(postgres_schema)
    store = PostgresVectorBackend(postgres_schema.dsn).open(postgres_schema.name, _ID)
    try:
        with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("UPDATE {}.vector_meta SET generation = 9").format(
                    sql.Identifier(postgres_schema.name)
                )
            )
        assert store.generation == 9
    finally:
        store.close()


def test_a_live_identity_change_is_incompatible(
    postgres_schema: PostgresSchema,
) -> None:
    _plant(postgres_schema)
    store = PostgresVectorBackend(postgres_schema.dsn).open(postgres_schema.name, _ID)
    try:
        with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL(
                    "UPDATE {}.vector_meta SET identity = jsonb_set(identity, '{{model}}', '\"other\"')"
                ).format(sql.Identifier(postgres_schema.name))
            )
        with pytest.raises(StoreIncompatibleError):
            _ = store.generation
    finally:
        store.close()


def test_tables_created_after_an_empty_open_are_seen(
    postgres_schema: PostgresSchema,
) -> None:
    store = PostgresVectorBackend(postgres_schema.dsn).open(postgres_schema.name, _ID)
    try:
        assert store.generation == 0
        _plant(postgres_schema, generation=4)
        assert store.generation == 4
    finally:
        store.close()


def test_close_is_idempotent_and_ends_reads(postgres_schema: PostgresSchema) -> None:
    store = PostgresVectorBackend(postgres_schema.dsn).open(postgres_schema.name, _ID)
    store.close()
    store.close()
    with pytest.raises(RuntimeError, match="closed"):
        _ = store.generation


def test_checkpoint_does_nothing(postgres_schema: PostgresSchema) -> None:
    store = PostgresVectorBackend(postgres_schema.dsn).open(postgres_schema.name, _ID)
    try:
        store.checkpoint()
    finally:
        store.close()


def test_opening_logs_once_without_the_dsn(
    postgres_schema: PostgresSchema, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="markdown_vault_mcp.stores.postgres_vectors")
    store = PostgresVectorBackend(postgres_schema.dsn).open(postgres_schema.name, _ID)
    store.close()
    (record,) = [
        r for r in caplog.records if r.msg.startswith("postgres_vector_store_opened")
    ]
    assert record.args == (postgres_schema.name, 0, False, False)
    password = conninfo_to_dict(postgres_schema.dsn).get("password")
    assert password
    assert str(password) not in caplog.text


def test_the_store_never_shows_its_dsn(postgres_schema: PostgresSchema) -> None:
    backend = PostgresVectorBackend(postgres_schema.dsn)
    store = backend.open(postgres_schema.name, _ID)
    try:
        password = str(conninfo_to_dict(postgres_schema.dsn).get("password"))
        assert password not in repr(store)
        assert password not in repr(backend)
    finally:
        store.close()


def test_importing_without_the_extra_names_it() -> None:
    code = (
        "import sys; sys.modules['psycopg'] = None\n"
        "try:\n"
        "    import markdown_vault_mcp.stores.postgres_vectors\n"
        "except ImportError as exc:\n"
        "    print(exc)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )
    assert "markdown-vault-mcp[postgres]" in out.stdout


class _Raising:
    """A stand-in for ``pool.connection()`` that raises *exc* on entry."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __call__(self) -> _Raising:
        return self

    def __enter__(self) -> None:
        raise self._exc

    def __exit__(self, *_args: object) -> None:
        return None


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (
            psycopg.OperationalError("server closed the connection unexpectedly"),
            "connect",
        ),
        (PoolTimeout("couldn't get a connection after 30.00 sec"), "pool"),
    ],
)
def test_a_read_that_cannot_reach_postgresql_is_unavailable(
    postgres_schema: PostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
    exc: BaseException,
    reason: str,
) -> None:
    store = PostgresVectorBackend(postgres_schema.dsn).open(postgres_schema.name, _ID)
    try:
        monkeypatch.setattr(store._conn.pool, "connection", _Raising(exc))
        with pytest.raises(StoreUnavailableError, match=f"reason={reason}"):
            _ = store.generation
    finally:
        monkeypatch.undo()
        store.close()


def test_tables_dropped_between_the_layout_and_meta_reads_open_empty(
    postgres_schema: PostgresSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.stores.postgres_vectors as module

    _plant(postgres_schema, generation=5)
    monkeypatch.setattr(module, "read_meta", lambda *_a, **_k: None)
    store = PostgresVectorBackend(postgres_schema.dsn).open(
        postgres_schema.name, _UNKNOWN_DIM
    )
    try:
        assert store.identity.dimension == 0  # the planted 3 was not read
    finally:
        store.close()


def test_a_failed_create_privilege_lookup_is_unavailable(
    postgres_schema: PostgresSchema,
) -> None:
    from markdown_vault_mcp.stores.postgres_vectors import _require_create

    conninfo = pg.prepare_conninfo(postgres_schema.dsn, schema=postgres_schema.name)
    closed = pg.connect(conninfo, schema=postgres_schema.name)
    closed.close()
    with pytest.raises(StoreUnavailableError, match="reason=connect"):
        _require_create(closed, schema=postgres_schema.name)


def test_open_fresh_reads_nothing_of_the_store(postgres_schema: PostgresSchema) -> None:
    _plant(postgres_schema, generation=7)
    fresh = PostgresVectorBackend(postgres_schema.dsn).open_fresh(
        postgres_schema.name, _ID
    )
    try:
        assert fresh.generation == 0
        assert fresh.identity == _ID
        assert not fresh.read_only
    finally:
        fresh.close()
    with psycopg.connect(postgres_schema.dsn) as admin:
        row = admin.execute(
            sql.SQL("SELECT generation FROM {}.vector_meta").format(
                sql.Identifier(postgres_schema.name)
            )
        ).fetchone()
    assert row == (7,)


def test_open_fresh_over_a_corrupt_store_opens(postgres_schema: PostgresSchema) -> None:
    _plant(postgres_schema)
    with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP TABLE {}.vector_chunks").format(
                sql.Identifier(postgres_schema.name)
            )
        )
    fresh = PostgresVectorBackend(postgres_schema.dsn).open_fresh(
        postgres_schema.name, _ID
    )
    fresh.close()


def test_open_fresh_still_refuses_a_missing_schema(
    postgres_server: PostgresServer,
) -> None:
    backend = PostgresVectorBackend(postgres_server.dsn)
    with pytest.raises(StoreNotConfiguredError, match="CREATE SCHEMA"):
        backend.open_fresh("mvm_test_no_such_schema", _ID)


def test_open_fresh_without_a_schema_is_not_configured() -> None:
    backend = PostgresVectorBackend("host=db")
    with pytest.raises(StoreNotConfiguredError):
        backend.open_fresh(None, _ID)


def test_an_open_behind_a_lock_times_out_instead_of_hanging(
    postgres_schema: PostgresSchema,
) -> None:
    _plant(postgres_schema)
    holder = psycopg.connect(postgres_schema.dsn)
    try:
        holder.execute(
            sql.SQL("LOCK TABLE {}.vector_meta IN ACCESS EXCLUSIVE MODE").format(
                sql.Identifier(postgres_schema.name)
            )
        )
        backend = PostgresVectorBackend(
            postgres_schema.dsn, pool=pg.PoolSettings(statement_timeout_s=1)
        )
        with pytest.raises(StoreUnavailableError, match="reason=timeout"):
            backend.open(postgres_schema.name, _ID)
    finally:
        holder.close()


def test_a_store_opens_with_the_extension_outside_public(
    postgres_server: PostgresServer,
) -> None:
    if not postgres_server.throwaway:
        pytest.skip("creates a database: runs only against the harness's own container")
    database = f"mvm_test_extschema_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(postgres_server.dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                sql.Identifier(database)
            )
        )
    try:
        dsn = make_conninfo(postgres_server.dsn, dbname=database)
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute("CREATE SCHEMA vext")
            admin.execute("CREATE EXTENSION vector SCHEMA vext")
            admin.execute("CREATE SCHEMA mvm_test_vault")
        conninfo = pg.prepare_conninfo(dsn, schema="mvm_test_vault")
        with pg.connect(conninfo, schema="mvm_test_vault") as conn, conn.transaction():
            ext = pg.find_extension(conn, schema="mvm_test_vault")
            tables.create_tables(
                conn, schema="mvm_test_vault", extension=ext, identity=_ID
            )
        assert ext.schema == "vext"
        store = PostgresVectorBackend(dsn).open("mvm_test_vault", _ID)
        try:
            assert store.generation == 0
        finally:
            store.close()
    finally:
        with psycopg.connect(postgres_server.dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(database)
                )
            )


def test_timeouts_that_cannot_be_set_are_unavailable(
    postgres_schema: PostgresSchema,
) -> None:
    from markdown_vault_mcp.stores.postgres_vectors import _bound_waits

    conninfo = pg.prepare_conninfo(postgres_schema.dsn, schema=postgres_schema.name)
    closed = pg.connect(conninfo, schema=postgres_schema.name)
    closed.close()
    settings = pg.PoolSettings()
    with pytest.raises(StoreUnavailableError, match="reason=connect"):
        _bound_waits(closed, schema=postgres_schema.name, settings=settings)


def _lease_free(schema: PostgresSchema) -> bool:
    with psycopg.connect(schema.dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT pg_try_advisory_lock((%s::bigint << 32) | n.oid::bigint)"
            " FROM pg_namespace n WHERE n.nspname = %s",
            (LEASE_CLASS, schema.name),
        ).fetchone()
        taken = bool(row and row[0])
        if taken:
            conn.execute(
                "SELECT pg_advisory_unlock((%s::bigint << 32) | n.oid::bigint)"
                " FROM pg_namespace n WHERE n.nspname = %s",
                (LEASE_CLASS, schema.name),
            )
    return taken


def test_a_writer_holds_the_lease_until_close(postgres_schema: PostgresSchema) -> None:
    store = PostgresVectorBackend(postgres_schema.dsn).open(postgres_schema.name, _ID)
    try:
        assert not _lease_free(postgres_schema)
        store._ensure_lease()
    finally:
        store.close()
    assert _lease_free(postgres_schema)


def test_a_second_writer_is_refused_and_a_reader_is_not(
    postgres_schema: PostgresSchema,
) -> None:
    backend = PostgresVectorBackend(postgres_schema.dsn)
    first = backend.open(postgres_schema.name, _ID)
    try:
        with pytest.raises(StoreUnavailableError, match="reason=lease"):
            backend.open(postgres_schema.name, _ID)
        with pytest.raises(StoreUnavailableError, match="reason=lease"):
            backend.open_fresh(postgres_schema.name, _ID)
        reader = backend.open(postgres_schema.name, _ID, owns_files=False)
        reader.close()
    finally:
        first.close()
    second = backend.open(postgres_schema.name, _ID)
    second.close()


def test_a_reader_has_no_lease(postgres_schema: PostgresSchema) -> None:
    store = PostgresVectorBackend(postgres_schema.dsn).open(
        postgres_schema.name, _ID, owns_files=False
    )
    try:
        assert _lease_free(postgres_schema)
        with pytest.raises(ReadOnlyError):
            store._ensure_lease()
    finally:
        store.close()


def test_open_fresh_takes_the_lease(postgres_schema: PostgresSchema) -> None:
    fresh = PostgresVectorBackend(postgres_schema.dsn).open_fresh(
        postgres_schema.name, _ID
    )
    try:
        assert not _lease_free(postgres_schema)
    finally:
        fresh.close()
    assert _lease_free(postgres_schema)


@pytest.mark.parametrize("failure", ["corrupt", "incompatible", "pool"])
def test_a_failed_writer_open_releases_the_lease(
    postgres_schema: PostgresSchema, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    import markdown_vault_mcp.stores.postgres_vectors as module

    _plant(postgres_schema)
    wanted = _ID
    if failure == "corrupt":
        with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP TABLE {}.vector_chunks").format(
                    sql.Identifier(postgres_schema.name)
                )
            )
    elif failure == "incompatible":
        wanted = EmbeddingIdentity("ollama", "other", "", "v1", 3)
    else:

        def no_pool(*_a: object, **_k: object) -> object:
            raise pg.unavailable("no pool", reason="pool", schema=postgres_schema.name)

        monkeypatch.setattr(module, "open_pool", no_pool)
    backend = PostgresVectorBackend(postgres_schema.dsn)
    with pytest.raises(
        (StoreCorruptError, StoreIncompatibleError, StoreUnavailableError)
    ) as failure:
        backend.open(postgres_schema.name, wanted)
    # Checked while the exception (and its traceback, which keeps the open's
    # frames and their locals alive) is still held, as a caller logging it
    # would hold it: only an explicit release frees the lease here.
    assert failure.value is not None
    assert _lease_free(postgres_schema)


def test_writers_of_two_schemas_do_not_block_each_other(
    postgres_server: PostgresServer,
) -> None:
    from tests.fixtures.postgres import scratch_schema

    backend = PostgresVectorBackend(postgres_server.dsn)
    with (
        scratch_schema(postgres_server.dsn) as one,
        scratch_schema(postgres_server.dsn) as two,
    ):
        a = backend.open(one, _ID)
        try:
            b = backend.open(two, _ID)
            b.close()
        finally:
            a.close()


def test_a_closed_store_refuses_lease_checks(postgres_schema: PostgresSchema) -> None:
    store = PostgresVectorBackend(postgres_schema.dsn).open(postgres_schema.name, _ID)
    store.close()
    with pytest.raises(RuntimeError, match="closed"):
        store._ensure_lease()
