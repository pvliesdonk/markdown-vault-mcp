"""A read-only donor store keyed by embedding input (#1505)."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np
import pytest

from markdown_vault_mcp.embed_text import EmbedTextBuilder
from markdown_vault_mcp.exceptions import StoreCorruptError
from markdown_vault_mcp.indexing.donor import Donor, DonorRefused
from markdown_vault_mcp.indexing.embedding_reconciler import stored_inputs
from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend
from markdown_vault_mcp.stores.sqlite_vectors import SqliteVectorBackend
from markdown_vault_mcp.types import DocumentRows, EmbeddingIdentity, VectorRow
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vectors import write_legacy_sidecar

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.postgres import PostgresSchema

_PROVIDER = MockEmbeddingProvider(dim=3)
_IDENT = EmbeddingIdentity.of(_PROVIDER, "v1")
_BUILDER = EmbedTextBuilder()
_ROWS = {
    "a.md": [
        ([3.0, 4.0, 0.0], {"title": "A", "heading": "One", "content": "apples"}),
        ([0.0, 1.0, 0.0], {"title": "A", "heading": "Two", "content": "pears"}),
    ],
    "b.md": [([0.0, 0.0, 2.0], {"title": "B", "content": "boats"})],
}
_LOGGER = "markdown_vault_mcp.indexing.donor"


class Source(NamedTuple):
    backend: Any
    location: Any
    files: list[Path]


def _documents() -> list[DocumentRows]:
    return [
        DocumentRows(
            path,
            f"t-{path}",
            tuple(
                VectorRow(vector, {**meta, "path": path, "folder": ""})
                for vector, meta in rows
            ),
        )
        for path, rows in _ROWS.items()
    ]


def _publish(backend: Any, location: Any) -> None:
    store = backend.open(location, _IDENT)
    store.publish_generation(_IDENT, _documents())
    store.close()


@pytest.fixture(params=["numpy_v1", "numpy_v2", "sqlite", "postgres"])
def source(request: pytest.FixtureRequest, tmp_path: Path) -> Source:
    kind = request.param
    if kind == "numpy_v1":
        base = tmp_path / "legacy-embeddings"
        vectors = [v for rows in _ROWS.values() for v, _ in rows]
        meta = [
            {**m, "path": p, "folder": ""} for p, rows in _ROWS.items() for _, m in rows
        ]
        write_legacy_sidecar(base, vectors, meta, provider=_PROVIDER)
        return Source(
            NumpySidecarBackend(),
            base,
            [base.with_suffix(".json"), base.with_suffix(".npy")],
        )
    if kind == "numpy_v2":
        base = tmp_path / "v2-embeddings"
        _publish(NumpySidecarBackend(), base)
        return Source(NumpySidecarBackend(), base, sorted(tmp_path.glob("v2-*")))
    if kind == "sqlite":
        base = tmp_path / "sq-embeddings"
        _publish(SqliteVectorBackend(), base)
        return Source(SqliteVectorBackend(), base, sorted(tmp_path.glob("sq-*")))
    schema: PostgresSchema = request.getfixturevalue("postgres_schema")
    from markdown_vault_mcp.stores.postgres_vectors import PostgresVectorBackend

    _publish(PostgresVectorBackend(schema.dsn), schema.name)
    return Source(PostgresVectorBackend(schema.dsn), schema.name, [])


def _refusals(caplog: pytest.LogCaptureFixture) -> list[Any]:
    return [
        r.args for r in caplog.records if r.msg.startswith("embeddings_donor_refused ")
    ]


def test_a_compatible_store_donates_every_row_by_input(source: Source) -> None:
    donor = Donor.open(source.backend, source.location, _IDENT, builder=_BUILDER)
    assert isinstance(donor, Donor)
    with donor:
        apples = _BUILDER.build(
            title="A",
            heading="One",
            content="apples",
            fields_text="",
            is_first_chunk=False,
        )
        given = donor.vectors_for("a.md")
        assert set(given) >= {apples}
        assert len(given) == 2
        assert np.allclose(given[apples], [0.6, 0.8, 0.0], atol=1e-6)
        assert len(donor.vectors_for("b.md")) == 1
        assert donor.refused_rows == 0


def test_the_donor_key_is_the_reconcilers_key(source: Source) -> None:
    donor = Donor.open(source.backend, source.location, _IDENT, builder=_BUILDER)
    assert isinstance(donor, Donor)
    reader = source.backend.open(source.location, _IDENT, owns_files=False)
    try:
        with donor:
            for path in _ROWS:
                expected = stored_inputs(_BUILDER, reader.rows_for(path))
                assert set(donor.vectors_for(path)) == set(expected)
    finally:
        reader.close()


def test_an_unknown_path_donates_nothing(source: Source) -> None:
    donor = Donor.open(source.backend, source.location, _IDENT, builder=_BUILDER)
    assert isinstance(donor, Donor)
    with donor:
        assert donor.vectors_for("missing.md") == {}


def test_another_model_is_refused_as_identity_differs(
    source: Source, caplog: pytest.LogCaptureFixture
) -> None:
    other = EmbeddingIdentity(_IDENT.provider, "other-model", "", "v1", 3)
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        donor = Donor.open(source.backend, source.location, other, builder=_BUILDER)
    assert isinstance(donor, DonorRefused)
    assert [args[1] for args in _refusals(caplog)] == ["identity_differs"]
    assert [r.levelno for r in caplog.records if r.name == _LOGGER] == [logging.WARNING]


def test_the_donor_never_writes(source: Source) -> None:
    expected = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.files}
    names = {p.name for p in source.files}
    donor = Donor.open(source.backend, source.location, _IDENT, builder=_BUILDER)
    assert isinstance(donor, Donor)
    with donor:
        donor.vectors_for("a.md")
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.files}
    assert after == expected
    if source.files:
        parent = source.files[0].parent
        stem = source.files[0].name.split(".")[0]
        added = {p for p in parent.glob(f"{stem}*") if p.name not in names}
        # A read-only SQLite reader of a WAL database leaves SQLite's own
        # shared-memory index and an empty -wal; nothing is written to them.
        assert {p.name.rsplit("-", 1)[-1] for p in added} <= {"shm", "wal"}
        assert all(p.stat().st_size == 0 for p in added if p.name.endswith("-wal"))


def test_a_bare_list_sidecar_is_refused_as_identity_unknown(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    base = tmp_path / "legacy-embeddings"
    base.with_suffix(".json").write_text(json.dumps([{"path": "a.md"}]))
    np.save(base.with_suffix(".npy"), np.asarray([[1, 0, 0]], dtype=np.float32))
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        donor = Donor.open(NumpySidecarBackend(), base, _IDENT, builder=_BUILDER)
    assert isinstance(donor, DonorRefused)
    assert [args[1] for args in _refusals(caplog)] == ["identity_unknown"]


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        ("garbage", "corrupt"),
        ("parent_is_a_file", "unavailable"),
        ("newer_format", "unsupported"),
    ],
)
def test_an_unreadable_source_is_refused_with_its_reason(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, damage: str, reason: str
) -> None:
    base = tmp_path / "vectors" / "idx-embeddings"
    if damage == "parent_is_a_file":
        (tmp_path / "vectors").write_text("not a directory")
    else:
        _publish(NumpySidecarBackend(), base)
        commit = base.with_suffix(".v2.json")
        if damage == "garbage":
            commit.write_text("{not json")
        else:
            payload = json.loads(commit.read_text())
            payload["format_version"] = 99
            commit.write_text(json.dumps(payload))
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        donor = Donor.open(NumpySidecarBackend(), base, _IDENT, builder=_BUILDER)
    assert isinstance(donor, DonorRefused)
    assert [args[1] for args in _refusals(caplog)] == [reason]


def test_a_missing_postgres_schema_is_refused_as_not_configured(
    postgres_schema: PostgresSchema, caplog: pytest.LogCaptureFixture
) -> None:
    from markdown_vault_mcp.stores.postgres_vectors import PostgresVectorBackend

    backend = PostgresVectorBackend(postgres_schema.dsn)
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        donor = Donor.open(backend, "no_such_schema", _IDENT, builder=_BUILDER)
    assert isinstance(donor, DonorRefused)
    assert _refusals(caplog) == [("postgres:no_such_schema", "not_configured")]


def test_a_postgres_donor_takes_no_lease(postgres_schema: PostgresSchema) -> None:
    from markdown_vault_mcp.stores.postgres_vectors import PostgresVectorBackend
    from tests.fixtures.postgres import lease_is_free

    _publish(PostgresVectorBackend(postgres_schema.dsn), postgres_schema.name)
    backend = PostgresVectorBackend(postgres_schema.dsn)
    donor = Donor.open(backend, postgres_schema.name, _IDENT, builder=_BUILDER)
    assert isinstance(donor, Donor)
    with donor:
        assert lease_is_free(postgres_schema.dsn, postgres_schema.name)


def test_a_postgres_refusal_never_shows_the_dsn(
    postgres_schema: PostgresSchema, caplog: pytest.LogCaptureFixture
) -> None:
    from psycopg.conninfo import conninfo_to_dict

    from markdown_vault_mcp.stores.postgres_vectors import PostgresVectorBackend

    backend = PostgresVectorBackend(postgres_schema.dsn)
    with caplog.at_level(logging.DEBUG):
        Donor.open(backend, "no_such_schema", _IDENT, builder=_BUILDER)
    secret = conninfo_to_dict(postgres_schema.dsn).get("password")
    assert postgres_schema.dsn not in caplog.text
    if secret:
        assert str(secret) not in caplog.text


class _FakeStore:
    def __init__(self, rows: list[VectorRow]) -> None:
        self._rows = rows
        self.identity = _IDENT
        self.count = max(len(rows), 1)
        self.closed = False

    def rows_for(self, path: str) -> list[VectorRow]:
        return self._rows if path == "a.md" else []

    def close(self) -> None:
        self.closed = True


class _FakeBackend:
    name = "fake"

    def __init__(self, store: Any = None, error: Exception | None = None) -> None:
        self.store, self.error, self.calls = store, error, []

    def open(self, _location: Any, _identity: Any, *, owns_files: bool = True) -> Any:
        self.calls.append(owns_files)
        if self.error is not None:
            raise self.error
        return self.store


def test_bad_rows_are_counted_not_donated() -> None:
    good = {"title": "A", "content": "good"}
    rows = [
        VectorRow([0.6, 0.8, 0.0], good),
        VectorRow([0.0, 0.0, 0.0], {"title": "A", "content": "zero"}),
        VectorRow([float("nan"), 1.0, 0.0], {"title": "A", "content": "nan"}),
        VectorRow([1.0, 0.0], {"title": "A", "content": "short"}),
    ]
    donor = Donor.open(_FakeBackend(_FakeStore(rows)), "x", _IDENT, builder=_BUILDER)
    assert isinstance(donor, Donor)
    given = donor.vectors_for("a.md")
    key = _BUILDER.build(
        title="A", heading=None, content="good", fields_text="", is_first_chunk=False
    )
    assert list(given) == [key]
    assert given[key].dtype == np.float32
    assert donor.refused_rows == 3


def test_a_donor_opens_read_only_and_closes_its_store() -> None:
    store = _FakeStore([])
    backend = _FakeBackend(store)
    donor = Donor.open(backend, "x", _IDENT, builder=_BUILDER)
    assert isinstance(donor, Donor)
    assert backend.calls == [False]
    donor.close()
    donor.close()
    assert store.closed


def test_a_store_that_fails_the_identity_check_is_closed() -> None:
    store = _FakeStore([])
    store.identity = EmbeddingIdentity(_IDENT.provider, "other", "", "v1", 3)
    donor = Donor.open(_FakeBackend(store), "x", _IDENT, builder=_BUILDER)
    assert isinstance(donor, DonorRefused)
    assert store.closed


def test_an_untyped_open_outcome_is_refused_as_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from markdown_vault_mcp.exceptions import StoreOpenError

    error = StoreOpenError("odd", backend="fake", location="x")
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        donor = Donor.open(_FakeBackend(error=error), "x", _IDENT, builder=_BUILDER)
    assert isinstance(donor, DonorRefused)
    assert _refusals(caplog) == [("fake:x", "unavailable")]


def test_the_source_label_names_the_backend_and_location() -> None:
    error = StoreCorruptError("bad", backend="fake", location="x")
    donor = Donor.open(_FakeBackend(error=error), "x", _IDENT, builder=_BUILDER)
    assert isinstance(donor, DonorRefused)
    donor = Donor.open(_FakeBackend(_FakeStore([])), "x", _IDENT, builder=_BUILDER)
    assert isinstance(donor, Donor)
    assert donor.source == "fake:x"


class TestReviewFindings:
    """#1505 child 3's final review."""

    @pytest.mark.parametrize("kind", ["numpy", "sqlite", "memory"])
    def test_a_missing_or_empty_source_is_refused_as_empty(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture, kind: str
    ) -> None:
        backend = SqliteVectorBackend() if kind == "sqlite" else NumpySidecarBackend()
        location = ":memory:" if kind == "memory" else tmp_path / "typo-embeddings"
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            outcome = Donor.open(backend, location, _IDENT, builder=_BUILDER)
        assert isinstance(outcome, DonorRefused)
        assert outcome.reason == "empty"
        assert [args[1] for args in _refusals(caplog)] == ["empty"]
        assert not list(tmp_path.iterdir())

    def test_an_empty_postgres_schema_is_refused_as_empty(
        self, postgres_schema: PostgresSchema
    ) -> None:
        from markdown_vault_mcp.stores.postgres_vectors import PostgresVectorBackend

        backend = PostgresVectorBackend(postgres_schema.dsn)
        outcome = Donor.open(backend, postgres_schema.name, _IDENT, builder=_BUILDER)
        assert isinstance(outcome, DonorRefused)
        assert outcome.reason == "empty"

    def test_a_refusal_carries_what_was_recorded_and_wanted(
        self, source: Source
    ) -> None:
        other = EmbeddingIdentity(_IDENT.provider, "other-model", "", "v1", 3)
        outcome = Donor.open(source.backend, source.location, other, builder=_BUILDER)
        assert isinstance(outcome, DonorRefused)
        assert outcome.reason == "identity_differs"
        assert "other-model" in outcome.detail
        assert _IDENT.model in outcome.detail

    def test_keys_follow_the_builders_format(self, tmp_path: Path) -> None:
        builder = EmbedTextBuilder(searchable_fields=("summary",))
        ident = EmbeddingIdentity.of(_PROVIDER, builder.format_token())
        base = tmp_path / "fmt-embeddings"
        store = NumpySidecarBackend().open(base, ident)
        meta = {"path": "a.md", "folder": "", "title": "A", "heading": "One"}
        store.publish_generation(
            ident,
            [
                DocumentRows(
                    "a.md",
                    "t",
                    (
                        VectorRow(
                            [1.0, 0, 0], {**meta, "content": "c1", "preamble": "p"}
                        ),
                        VectorRow(
                            [0, 1.0, 0], {**meta, "heading": "Two", "content": "c2"}
                        ),
                    ),
                )
            ],
        )
        store.close()
        outcome = Donor.open(NumpySidecarBackend(), base, ident, builder=builder)
        assert isinstance(outcome, Donor)
        with outcome:
            assert set(outcome.vectors_for("a.md")) == {
                builder.build(
                    title="A",
                    heading="One",
                    content="c1",
                    fields_text="p",
                    is_first_chunk=True,
                ),
                builder.build(
                    title="A",
                    heading="Two",
                    content="c2",
                    fields_text="",
                    is_first_chunk=False,
                ),
            }

    def test_a_postgres_donor_changes_no_rows(
        self, postgres_schema: PostgresSchema
    ) -> None:
        from markdown_vault_mcp.stores.postgres_vectors import PostgresVectorBackend

        backend = PostgresVectorBackend(postgres_schema.dsn)
        _publish(backend, postgres_schema.name)
        reader = backend.open(postgres_schema.name, _IDENT, owns_files=False)
        try:
            expected = (reader.generation, reader.tokens(), reader.count)
            outcome = Donor.open(
                backend, postgres_schema.name, _IDENT, builder=_BUILDER
            )
            assert isinstance(outcome, Donor)
            with outcome:
                outcome.vectors_for("a.md")
            assert (reader.generation, reader.tokens(), reader.count) == expected
        finally:
            reader.close()
