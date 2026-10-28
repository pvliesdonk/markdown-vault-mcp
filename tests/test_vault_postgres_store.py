"""A vault whose vectors live in PostgreSQL (#1504)."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vault_store_contract import VaultStoreContract

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.postgres import PostgresSchema


class _Counting(MockEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__(dim=16)
        self.embedded = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embedded += len(texts)
        return super().embed(texts)


def _settings(tmp_path: Path, schema: PostgresSchema, **kw: object) -> VaultSettings:
    values: dict[str, object] = {
        "index_path": tmp_path / "index.db",
        "read_only": False,
        "embedding_store": "postgres",
        "embedding_search": "streamed",
        "embedding_postgres_dsn": schema.dsn,
        "embedding_postgres_schema": schema.name,
        **kw,
    }
    return VaultSettings(**values)  # type: ignore[arg-type]


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "alpha.md").write_text("# Alpha\n\nApples and pears.\n")
    (root / "beta.md").write_text("# Beta\n\nBoats and harbours.\n")
    return root


def test_a_vault_on_postgres_keeps_its_vectors_across_a_restart(
    tmp_path: Path, postgres_schema: PostgresSchema
) -> None:
    root, provider = _root(tmp_path), _Counting()

    def vault() -> Vault:
        return Vault(
            source_dir=root,
            settings=_settings(tmp_path, postgres_schema),
            embedding_provider=provider,
        )

    first = vault()
    try:
        first.index.build_index()
        first.index.build_embeddings()
        assert first.reader.search("pears", mode="semantic")
        assert first._vector_slot.location == postgres_schema.name
    finally:
        first.close()
    embedded = provider.embedded
    second = vault()
    try:
        second.index.build_index()
        assert second.reader.search("pears", mode="semantic")
    finally:
        second.close()
    assert provider.embedded == embedded + 1  # only the query: nothing re-embedded


def test_resident_search_on_postgres_is_refused(
    tmp_path: Path, postgres_schema: PostgresSchema
) -> None:
    settings = _settings(tmp_path, postgres_schema, embedding_search="resident")
    root, provider = _root(tmp_path), MockEmbeddingProvider(dim=16)
    with pytest.raises(ConfigurationError, match="streamed"):
        Vault(source_dir=root, settings=settings, embedding_provider=provider)


@pytest.mark.parametrize(
    ("missing", "named"),
    [
        ("embedding_postgres_dsn", "embedding_postgres_dsn"),
        ("embedding_postgres_schema", "embedding_postgres_schema"),
    ],
)
def test_postgres_without_a_dsn_or_schema_is_refused(
    tmp_path: Path, missing: str, named: str
) -> None:
    settings = VaultSettings(
        index_path=tmp_path / "index.db",
        read_only=False,
        embedding_store="postgres",
        embedding_search="streamed",
        embedding_postgres_dsn="host=db",
        embedding_postgres_schema="vault_a",
    )
    import dataclasses

    settings = dataclasses.replace(settings, **{missing: None})
    root, provider = _root(tmp_path), MockEmbeddingProvider(dim=16)
    with pytest.raises(ConfigurationError, match=named):
        Vault(source_dir=root, settings=settings, embedding_provider=provider)


def test_postgres_without_the_extra_is_a_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "markdown_vault_mcp.stores.postgres_vectors", None)
    settings = VaultSettings(
        index_path=tmp_path / "index.db",
        read_only=False,
        embedding_store="postgres",
        embedding_search="streamed",
        embedding_postgres_dsn="host=db",
        embedding_postgres_schema="vault_a",
    )
    root, provider = _root(tmp_path), MockEmbeddingProvider(dim=16)
    with pytest.raises(ConfigurationError, match=r"postgres\]"):
        Vault(source_dir=root, settings=settings, embedding_provider=provider)


def test_the_dsn_never_shows(
    tmp_path: Path, postgres_schema: PostgresSchema, caplog: pytest.LogCaptureFixture
) -> None:
    from psycopg.conninfo import conninfo_to_dict

    secret = conninfo_to_dict(postgres_schema.dsn).get("password") or "<none>"
    settings = _settings(tmp_path, postgres_schema)
    assert postgres_schema.dsn not in repr(settings)
    with caplog.at_level(logging.DEBUG):
        vault = Vault(
            source_dir=_root(tmp_path),
            settings=settings,
            embedding_provider=MockEmbeddingProvider(dim=16),
        )
        try:
            vault.index.build_index()
            vault.index.build_embeddings()
        finally:
            vault.close()
    if secret != "<none>":
        assert secret not in caplog.text


@pytest.mark.parametrize(
    "pool",
    [
        {"embedding_postgres_pool_size": 0},
        {"embedding_postgres_statement_timeout_s": -1.0},
    ],
    ids=["pool_size", "statement_timeout"],
)
def test_postgres_pool_settings_out_of_range_are_refused(
    tmp_path: Path, pool: dict[str, object]
) -> None:
    settings = VaultSettings(
        index_path=tmp_path / "index.db",
        read_only=False,
        embedding_store="postgres",
        embedding_search="streamed",
        embedding_postgres_dsn="host=db",
        embedding_postgres_schema="vault_a",
        **pool,  # type: ignore[arg-type]
    )
    root, provider = _root(tmp_path), MockEmbeddingProvider(dim=16)
    with pytest.raises(ConfigurationError, match="pool settings refused"):
        Vault(source_dir=root, settings=settings, embedding_provider=provider)


def test_a_schema_with_surrounding_space_is_used_stripped(
    tmp_path: Path, postgres_schema: PostgresSchema
) -> None:
    settings = _settings(
        tmp_path,
        postgres_schema,
        embedding_postgres_schema=f"  {postgres_schema.name} ",
    )
    vault = Vault(
        source_dir=_root(tmp_path),
        settings=settings,
        embedding_provider=MockEmbeddingProvider(dim=16),
    )
    try:
        assert vault._vector_slot.location == postgres_schema.name
    finally:
        vault.close()


def test_pool_settings_refused_name_their_variables(tmp_path: Path) -> None:
    settings = VaultSettings(
        index_path=tmp_path / "index.db",
        read_only=False,
        embedding_store="postgres",
        embedding_search="streamed",
        embedding_postgres_dsn="host=db",
        embedding_postgres_schema="vault_a",
        embedding_postgres_pool_size=0,
    )
    root, provider = _root(tmp_path), MockEmbeddingProvider(dim=16)
    with pytest.raises(ConfigurationError, match="EMBEDDING_POSTGRES_POOL_SIZE"):
        Vault(source_dir=root, settings=settings, embedding_provider=provider)


def test_a_broken_postgres_install_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "markdown_vault_mcp.stores.postgres_vectors", None)
    settings = VaultSettings(
        index_path=tmp_path / "index.db",
        read_only=False,
        embedding_store="postgres",
        embedding_search="streamed",
        embedding_postgres_dsn="host=db",
        embedding_postgres_schema="vault_a",
    )
    root, provider = _root(tmp_path), MockEmbeddingProvider(dim=16)
    with pytest.raises(
        ConfigurationError,
        match=r"postgres\].*import of markdown_vault_mcp\.stores\.postgres_vectors",
    ):
        Vault(source_dir=root, settings=settings, embedding_provider=provider)


def test_status_counts_an_unopened_postgres_store(
    tmp_path: Path, postgres_schema: PostgresSchema
) -> None:
    root = _root(tmp_path)

    def vault() -> Vault:
        return Vault(
            source_dir=root,
            settings=_settings(tmp_path, postgres_schema),
            embedding_provider=MockEmbeddingProvider(dim=16),
        )

    first = vault()
    try:
        first.index.build_index()
        first.index.build_embeddings()
        built = first.index.embeddings_status()["chunk_count"]
    finally:
        first.close()
    second = vault()
    try:
        status = second.index.embeddings_status()  # nothing opened the store yet
    finally:
        second.close()
    assert built > 0
    assert (status["chunk_count"], status["path"], status["backend"]) == (
        built,
        None,
        "postgres",
    )
    assert status["store_details"]["schema"] == postgres_schema.name


def test_status_of_an_unreachable_postgres_store_raises(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import StoreUnavailableError

    settings = VaultSettings(
        index_path=tmp_path / "index.db",
        read_only=False,
        embedding_store="postgres",
        embedding_search="streamed",
        embedding_postgres_dsn="host=127.0.0.1 port=1 connect_timeout=2",
        embedding_postgres_schema="vault_a",
    )
    vault = Vault(
        source_dir=_root(tmp_path),
        settings=settings,
        embedding_provider=MockEmbeddingProvider(dim=16),
    )
    try:
        with pytest.raises(StoreUnavailableError):
            vault.index.embeddings_status()
    finally:
        vault.close()


def test_status_of_a_store_of_another_model_counts_none_until_it_is_rebuilt(
    tmp_path: Path, postgres_schema: PostgresSchema, caplog: pytest.LogCaptureFixture
) -> None:
    root = _root(tmp_path)
    first = Vault(
        source_dir=root,
        settings=_settings(tmp_path, postgres_schema),
        embedding_provider=MockEmbeddingProvider(dim=16),
    )
    try:
        first.index.build_index()
        first.index.build_embeddings()
    finally:
        first.close()
    changed = Vault(  # another model: the stored vectors answer none of its queries
        source_dir=root,
        settings=_settings(tmp_path, postgres_schema),
        embedding_provider=MockEmbeddingProvider(dim=8),
    )
    try:
        with caplog.at_level(
            logging.INFO, logger="markdown_vault_mcp.managers.embeddings"
        ):
            status = changed.index.embeddings_status()  # reports, never raises
    finally:
        changed.close()
    assert (status["chunk_count"], status["backend"]) == (0, "postgres")
    (event,) = [
        r
        for r in caplog.records
        if r.msg.startswith("embeddings_status_store_not_counted ")
    ]
    assert event.args[1] == "StoreIncompatibleError"


def test_status_of_a_damaged_store_counts_none_and_warns(
    tmp_path: Path, postgres_schema: PostgresSchema, caplog: pytest.LogCaptureFixture
) -> None:
    import psycopg
    from psycopg import sql

    root = _root(tmp_path)
    first = Vault(
        source_dir=root,
        settings=_settings(tmp_path, postgres_schema),
        embedding_provider=MockEmbeddingProvider(dim=16),
    )
    try:
        first.index.build_index()
        first.index.build_embeddings()
    finally:
        first.close()
    with psycopg.connect(postgres_schema.dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("DROP TABLE {}.vector_chunks").format(
                sql.Identifier(postgres_schema.name)
            )
        )
    damaged = Vault(
        source_dir=root,
        settings=_settings(tmp_path, postgres_schema),
        embedding_provider=MockEmbeddingProvider(dim=16),
    )
    try:
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp.managers.embeddings"
        ):
            status = damaged.index.embeddings_status()
    finally:
        damaged.close()
    assert status["chunk_count"] == 0
    assert [
        r.args[1]
        for r in caplog.records
        if r.msg.startswith("embeddings_status_store_not_counted ")
    ] == ["StoreCorruptError"]


class TestPostgresVaultConformance(VaultStoreContract):
    @pytest.fixture
    def store_settings(self, postgres_schema: PostgresSchema) -> dict[str, Any]:
        return {
            "embedding_store": "postgres",
            "embedding_search": "streamed",
            "embedding_postgres_dsn": postgres_schema.dsn,
            "embedding_postgres_schema": postgres_schema.name,
        }
