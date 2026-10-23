"""``markdown-vault-mcp embeddings import`` (#1505 child 6b)."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

from markdown_vault_mcp.cli import _ENV_PREFIX, app
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.sidecar_v5_1.notes import NOTES

if TYPE_CHECKING:
    from collections.abc import Iterator

    from typer.testing import Result

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "sidecar_v5_1"
_ROOT = str(Path(__file__).resolve().parents[1])


class _Counting(MockEmbeddingProvider):
    """``mock/mock-dim-32``, counting embedded texts across the test."""

    texts: list[str] = []  # noqa: RUF012 - shared by every instance a run builds

    def embed(self, texts: list[str]) -> list[list[float]]:
        _Counting.texts.extend(texts)
        return super().embed(texts)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A vault of the fixture notes, an index path, a counting mock provider."""
    import markdown_vault_mcp.providers as providers_mod

    _Counting.texts = []
    monkeypatch.setattr(providers_mod, "get_embedding_provider", lambda _c: _Counting())
    root = tmp_path / "vault"
    for name, text in NOTES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", str(root))
    monkeypatch.setenv(f"{_ENV_PREFIX}_INDEX_PATH", str(tmp_path / "index.db"))
    monkeypatch.setenv(f"{_ENV_PREFIX}_EMBEDDING_PROVIDER", "fastembed")
    monkeypatch.delenv(f"{_ENV_PREFIX}_EMBEDDINGS_PATH", raising=False)
    yield tmp_path


def _store(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(f"{_ENV_PREFIX}_EMBEDDING_STORE", name)


def _sums(root: Path) -> dict[str, str]:
    # SQLite gives any reader of a WAL database its -wal and -shm files; they
    # hold no rows of their own.
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.name.endswith(("-wal", "-shm"))
    }


def _import(*args: str) -> Result:
    return runner.invoke(app, ["embeddings", "import", *args])


def _built_with(monkeypatch: pytest.MonkeyPatch, store: str) -> None:
    _store(monkeypatch, store)
    assert runner.invoke(app, ["index"]).exit_code == 0
    _Counting.texts = []


def _chunk_count(monkeypatch: pytest.MonkeyPatch, store: str) -> int:
    from markdown_vault_mcp.cli import _build_vault

    _store(monkeypatch, store)
    built = _build_vault()
    try:
        return int(built.vault.index.embeddings_status()["chunk_count"])
    finally:
        built.close()


_HOLDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend
    from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend
    from tests.fixtures.vector_store_contract import IDENT
    backend = {"numpy": NumpySidecarBackend, "sqlite": SqliteVectorBackend}[sys.argv[1]]
    store = backend().open(Path(sys.argv[2]), IDENT)
    print("held", flush=True)
    sys.stdin.readline()
    store.close()
    """
)


@pytest.fixture
def server(env: Path) -> Iterator[Any]:
    """Start another process holding the configured store's writer lock."""
    started: list[subprocess.Popen[str]] = []

    def start(kind: str = "sqlite") -> None:
        # One lock covers both stores at a base; the holder opens an empty one.
        process = subprocess.Popen(
            [sys.executable, "-c", _HOLDER, kind, str(env / "index-db-embeddings")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            cwd=_ROOT,
        )
        started.append(process)
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "held"

    try:
        yield start
    finally:
        for process in started:
            assert process.stdin is not None
            process.stdin.write("\n")
            process.stdin.flush()
            process.wait(timeout=30)


def _source_files(env: Path, store: str) -> dict[str, str]:
    pattern = "index-db-embeddings.v2.*" if store == "numpy" else "*.sqlite3"
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in env.glob(pattern)
    }


@pytest.mark.parametrize(("src", "dst"), [("numpy", "sqlite"), ("sqlite", "numpy")])
def test_an_import_between_built_in_stores_is_free(
    env: Path, monkeypatch: pytest.MonkeyPatch, src: str, dst: str
) -> None:
    _built_with(monkeypatch, src)
    source_files = _source_files(env, src)
    assert source_files
    _store(monkeypatch, dst)
    result = _import("--from", src)
    assert result.exit_code == 0, result.output
    assert "3 chunks, 3 reused, 0 embedded" in result.stdout
    assert _Counting.texts == []
    assert _chunk_count(monkeypatch, dst) == 3
    assert _source_files(env, src) == source_files  # the source is never written


@pytest.mark.parametrize("dst", ["numpy", "sqlite"])
def test_a_dry_run_writes_nothing(
    env: Path, monkeypatch: pytest.MonkeyPatch, dst: str
) -> None:
    src = "sqlite" if dst == "numpy" else "numpy"
    _built_with(monkeypatch, src)
    (env / "vault" / "alpha.md").write_text(NOTES["alpha.md"] + "\nEdited.\n")
    before = _sums(env)
    _store(monkeypatch, dst)
    result = _import("--from", src, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "Would import" in result.stdout
    assert "3 chunks, 2 reusable, 1 to embed. Nothing was written." in result.stdout
    assert _sums(env) == before  # no new file, none changed
    assert _Counting.texts == []


@pytest.mark.usefixtures("env")
def test_a_dry_run_works_while_a_server_writes(
    monkeypatch: pytest.MonkeyPatch, server: Any
) -> None:
    _built_with(monkeypatch, "numpy")
    server()
    _store(monkeypatch, "sqlite")
    result = _import("--from", "numpy", "--dry-run")
    assert result.exit_code == 0, result.output


def test_the_import_refuses_while_a_server_writes(
    env: Path, monkeypatch: pytest.MonkeyPatch, server: Any
) -> None:
    for suffix in (".json", ".npy"):
        shutil.copy(FIXTURE / f"index-db-embeddings{suffix}", env / f"released{suffix}")
    server()
    before = _sums(env)
    _store(monkeypatch, "sqlite")
    result = _import("--from", "numpy", "--from-path", str(env / "released"))
    assert result.exit_code == 1
    assert "Stop the server first" in result.stderr
    # Building the vault opens index.db, as any command does; nothing else.
    after = _sums(env)
    after.pop("index.db", None)
    assert after == before


@pytest.mark.usefixtures("env")
def test_the_same_store_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _built_with(monkeypatch, "numpy")
    result = _import("--from", "numpy")
    assert result.exit_code == 1
    assert "is the configured store" in result.stderr


def test_the_released_sidecar_imports_into_numpy(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for suffix in (".json", ".npy"):
        shutil.copy(
            FIXTURE / f"index-db-embeddings{suffix}",
            env / f"index-db-embeddings{suffix}",
        )
    _store(monkeypatch, "numpy")
    result = _import("--from", "numpy")
    assert result.exit_code == 0, result.output
    assert "0 embedded" in result.stdout


def _released_as(env: Path, payload: Any) -> Path:
    base = env / "elsewhere" / "emb"
    base.parent.mkdir()
    base.with_suffix(".json").write_text(json.dumps(payload))
    shutil.copy(FIXTURE / "index-db-embeddings.npy", base.with_suffix(".npy"))
    return base


def test_a_bare_list_is_refused(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = json.loads((FIXTURE / "index-db-embeddings.json").read_text())["rows"]
    base = _released_as(env, rows)
    before = _sums(base.parent)
    _store(monkeypatch, "sqlite")
    result = _import("--from", "numpy", "--from-path", str(base))
    assert result.exit_code == 1
    assert "records no embedding identity" in result.stderr
    assert _sums(base.parent) == before


def test_another_model_is_refused(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.loads((FIXTURE / "index-db-embeddings.json").read_text())
    payload["index_metadata"]["model"] = "mock-dim-other"
    base = _released_as(env, payload)
    _store(monkeypatch, "sqlite")
    result = _import("--from", "numpy", "--from-path", str(base))
    assert result.exit_code == 1
    assert "holds vectors of another embedding model" in result.stderr
    assert "Configure that provider" in result.stderr
    assert ".." not in result.stderr


def test_an_empty_source_is_refused(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _store(monkeypatch, "sqlite")
    result = _import("--from", "numpy", "--from-path", str(env / "nothing" / "here"))
    assert result.exit_code == 1
    assert "holds no vectors; nothing to import" in result.stderr


def test_an_in_memory_destination_is_refused(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(f"{_ENV_PREFIX}_INDEX_PATH")
    result = _import("--from", "numpy", "--from-path", str(env / "x"))
    assert result.exit_code == 1
    assert "keeps vectors in memory only" in result.stderr


@pytest.mark.usefixtures("env")
def test_index_reports_a_busy_store(
    monkeypatch: pytest.MonkeyPatch, server: Any
) -> None:
    server()
    _store(monkeypatch, "sqlite")
    result = runner.invoke(app, ["index"])
    assert result.exit_code == 1
    assert "Stop the server first" in result.stderr
    assert "Traceback" not in result.output


@pytest.mark.usefixtures("env")
def test_every_refusal_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _built_with(monkeypatch, "numpy")
    with caplog.at_level(logging.WARNING):
        _import("--from", "numpy")
    assert [
        r.args for r in caplog.records if r.msg.startswith("embeddings_import_refused")
    ] == [("same_store",)]


@pytest.mark.usefixtures("env")
def test_a_postgres_source_without_a_dsn_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MVM_TEST_DSN_UNSET", raising=False)
    _store(monkeypatch, "sqlite")
    result = _import(
        "--from",
        "postgres",
        "--from-schema",
        "x",
        "--from-dsn-env",
        "MVM_TEST_DSN_UNSET",
    )
    assert result.exit_code == 1
    assert "MVM_TEST_DSN_UNSET" in result.stderr


@pytest.mark.usefixtures("env")
def test_a_postgres_source_never_shows_its_dsn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pytest.importorskip("psycopg")
    monkeypatch.setenv("MVM_TEST_DSN", "postgresql://u:secret@127.0.0.1:1/x")
    _store(monkeypatch, "sqlite")
    with caplog.at_level(logging.DEBUG):
        result = _import(
            "--from", "postgres", "--from-schema", "x", "--from-dsn-env", "MVM_TEST_DSN"
        )
    assert result.exit_code == 1
    assert "secret" not in result.output
    assert not [r for r in caplog.records if "secret" in r.getMessage()]


def _postgres(monkeypatch: pytest.MonkeyPatch, schema: Any) -> None:
    monkeypatch.setenv(f"{_ENV_PREFIX}_EMBEDDING_POSTGRES_DSN", schema.dsn)
    monkeypatch.setenv(f"{_ENV_PREFIX}_EMBEDDING_POSTGRES_SCHEMA", schema.name)


def test_import_postgres_into_sqlite_and_back(
    env: Path, monkeypatch: pytest.MonkeyPatch, postgres_schema: Any
) -> None:
    _postgres(monkeypatch, postgres_schema)
    _built_with(monkeypatch, "postgres")
    _store(monkeypatch, "sqlite")
    into_sqlite = _import("--from", "postgres")
    assert into_sqlite.exit_code == 0, into_sqlite.output
    assert "3 reused, 0 embedded" in into_sqlite.stdout
    assert _chunk_count(monkeypatch, "postgres") == 3  # the source keeps its rows
    _store(monkeypatch, "postgres")
    back = _import("--from", "sqlite", "--from-path", str(env / "index-db-embeddings"))
    assert back.exit_code == 0, back.output
    assert "0 embedded" in back.stdout
    assert _Counting.texts == []
    assert str(postgres_schema.dsn) not in into_sqlite.output + back.output


def test_a_running_servers_lease_refuses(
    env: Path, monkeypatch: pytest.MonkeyPatch, postgres_schema: Any
) -> None:
    from markdown_vault_mcp.cli import _build_vault

    _postgres(monkeypatch, postgres_schema)
    _built_with(monkeypatch, "sqlite")
    _store(monkeypatch, "postgres")
    held = _build_vault()  # this process's own writer stands in for the server
    try:
        held.vault.index.build_index()
        held.vault.index.build_embeddings()
        result = _import(
            "--from", "sqlite", "--from-path", str(env / "index-db-embeddings")
        )
    finally:
        held.close()
    assert result.exit_code == 1
    assert "Stop the server first" in result.stderr


def test_reindex_reports_a_busy_store(
    env: Path, monkeypatch: pytest.MonkeyPatch, server: Any
) -> None:
    del env
    _built_with(monkeypatch, "sqlite")
    server("numpy")
    result = runner.invoke(app, ["reindex"])
    assert result.exit_code == 1
    assert "Stop the server first, then run reindex again" in result.stderr


@pytest.mark.usefixtures("env")
def test_no_provider_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    import markdown_vault_mcp.providers as providers_mod

    monkeypatch.setattr(providers_mod, "get_embedding_provider", lambda _c: None)
    result = _import("--from", "sqlite")
    assert result.exit_code == 1
    assert "no embedding provider is configured" in result.stderr


@pytest.mark.usefixtures("env")
def test_a_failed_batch_over_a_populated_store_exits_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _built_with(monkeypatch, "numpy")
    _store(monkeypatch, "sqlite")
    assert runner.invoke(app, ["index"]).exit_code == 0  # sqlite holds rows too
    from markdown_vault_mcp.exceptions import EmbeddingsNotPublishedError
    from markdown_vault_mcp.managers import embedding_import

    def kept(*_a: Any, **_k: Any) -> Any:
        raise EmbeddingsNotPublishedError(failed_batches=1)

    monkeypatch.setattr(embedding_import, "import_embeddings", kept)
    result = _import("--from", "numpy")
    assert result.exit_code == 1
    assert "Retry when the provider recovers." in result.stderr


@pytest.mark.usefixtures("env")
def test_a_destination_that_will_not_open_exits_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError
    from markdown_vault_mcp.managers import embedding_import

    def down(*_a: Any, **_k: Any) -> Any:
        raise StoreUnavailableError("database down", backend="postgres", location="v")

    monkeypatch.setattr(embedding_import, "import_embeddings", down)
    _store(monkeypatch, "sqlite")
    result = _import("--from", "numpy")
    assert result.exit_code == 1
    assert "the configured store cannot be opened: database down" in result.stderr


def _postgres_configured(monkeypatch: pytest.MonkeyPatch, *, schema: str = "v") -> None:
    pytest.importorskip("psycopg")
    _store(monkeypatch, "postgres")
    monkeypatch.setenv(
        f"{_ENV_PREFIX}_EMBEDDING_POSTGRES_DSN", "postgresql://u@127.0.0.1:1/x"
    )
    monkeypatch.setenv(f"{_ENV_PREFIX}_EMBEDDING_POSTGRES_SCHEMA", schema)


@pytest.mark.usefixtures("env")
def test_a_built_in_source_beside_a_postgres_store_needs_its_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _postgres_configured(monkeypatch)
    result = _import("--from", "numpy")
    assert result.exit_code == 1
    assert "name its base path with --from-path" in result.stderr


@pytest.mark.usefixtures("env")
def test_a_postgres_source_needs_a_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MVM_TEST_DSN", "postgresql://u@127.0.0.1:1/x")
    monkeypatch.delenv(f"{_ENV_PREFIX}_EMBEDDING_POSTGRES_SCHEMA", raising=False)
    _store(monkeypatch, "sqlite")
    result = _import("--from", "postgres", "--from-dsn-env", "MVM_TEST_DSN")
    assert result.exit_code == 1
    assert "name it with --from-schema" in result.stderr


@pytest.mark.usefixtures("env")
def test_a_postgres_source_that_cannot_be_configured_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import markdown_vault_mcp.vault as vault_mod
    from markdown_vault_mcp.exceptions import ConfigurationError

    def missing(_settings: Any) -> Any:
        raise ConfigurationError("The postgres store needs the postgres extra.")

    monkeypatch.setattr(vault_mod, "_postgres_backend", missing)
    monkeypatch.setenv("MVM_TEST_DSN", "postgresql://u@127.0.0.1:1/x")
    _store(monkeypatch, "sqlite")
    result = _import(
        "--from", "postgres", "--from-schema", "v", "--from-dsn-env", "MVM_TEST_DSN"
    )
    assert result.exit_code == 1
    assert "needs the postgres extra" in result.stderr


def test_two_dsns_naming_one_server_are_the_same_store() -> None:
    from markdown_vault_mcp.managers.embedding_import import _server

    assert _server("postgresql://a@db:5432/v") == _server("host=db dbname=v user=b")
    assert _server("not = a = dsn") == ("not = a = dsn", "", "")
