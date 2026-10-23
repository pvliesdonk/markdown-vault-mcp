"""The one writer of embedding rows (#1768)."""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from markdown_vault_mcp.embed_text import EmbedTextBuilder
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.indexing.embedding_reconciler import EmbeddingReconciler
from markdown_vault_mcp.scanner import HeadingChunker, parse_note
from markdown_vault_mcp.stores.numpy_sidecar import NumpySidecarBackend
from markdown_vault_mcp.types import (
    ANY_TOKEN,
    DocumentRows,
    EmbeddingIdentity,
    SkippedFile,
    SourceState,
    VectorRow,
)
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vectors import metadata_by_path

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class _Counting(MockEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__(dim=8)
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return super().embed(texts)


_OPEN: list[FTSIndex] = []


@pytest.fixture(autouse=True)
def _close_indexes() -> Iterator[None]:
    """Close every keyword index a test built through :func:`_index`."""
    yield
    while _OPEN:
        _OPEN.pop().close()


def _index(
    tmp_path: Path, notes: dict[str, str], chunker: HeadingChunker | None = None
) -> FTSIndex:
    fts = FTSIndex(db_path=":memory:")
    _OPEN.append(fts)
    for name, text in notes.items():
        (tmp_path / name).write_text(text)
        fts.upsert_note(
            parse_note(tmp_path / name, tmp_path, chunker or HeadingChunker())
        )
    return fts


class _Probe:
    """A SourceProbe answering from a dict; unknown paths are present."""

    def __init__(self, answers: dict[str, str] | None = None) -> None:
        self.answers = answers or {}
        self.asked: list[str] = []

    def probe(self, path: str) -> SourceState:
        self.asked.append(path)
        status = self.answers.get(path, "present")
        return SourceState(status, "t" if status == "present" else None)  # type: ignore[arg-type]


def _setup(
    tmp_path: Path,
    notes: dict[str, str],
    probe: _Probe | None = None,
    excluded: tuple[str, ...] = (),
    *,
    chunker: HeadingChunker | None = None,
    builder: EmbedTextBuilder | None = None,
):
    fts = _index(tmp_path, notes, chunker)
    provider = _Counting()
    builder = builder or EmbedTextBuilder()
    store = NumpySidecarBackend().open(
        None, EmbeddingIdentity.of(provider, builder.format_token())
    )
    reconciler = EmbeddingReconciler(
        fts=fts,
        provider=provider,
        embed_builder=builder,
        batch_size=4,
        source_probe=probe or _Probe(),
        is_excluded=lambda p: p in excluded,
    )
    return fts, provider, store, reconciler


NOTES = {"a.md": "# A\n\nApples.\n", "b.md": "# B\n\nBoats.\n"}


class TestDecisions:
    def test_new_documents_are_embedded_under_their_token(self, tmp_path):
        fts, _provider, store, rec = _setup(tmp_path, NOTES)
        result = rec.reconcile(store, site="test")
        assert result.embedded == 2
        hashes = {r["path"]: r["content_hash"] for r in fts.list_chunks()}
        assert store.tokens() == hashes

    def test_a_converged_store_costs_nothing(self, tmp_path):
        _, provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        provider.texts.clear()
        result = rec.reconcile(store, site="test")
        assert (result.embedded, result.up_to_date, provider.texts) == (0, 2, [])

    def test_a_changed_token_with_the_same_input_is_retokened_without_embedding(
        self, tmp_path
    ):
        fts, provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        (tmp_path / "a.md").write_text(
            "---\nmodified: 2026-10-12\n---\n# A\n\nApples.\n"
        )
        fts.upsert_note(parse_note(tmp_path / "a.md", tmp_path, HeadingChunker()))
        provider.texts.clear()
        result = rec.reconcile(store, ["a.md"], site="test")
        assert (result.retokened, provider.texts) == (1, [])
        assert (
            store.tokens()["a.md"] == fts.list_chunks(paths=["a.md"])[0]["content_hash"]
        )

    def test_a_changed_input_is_reembedded(self, tmp_path):
        fts, provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        (tmp_path / "a.md").write_text("# A\n\nQuinces.\n")
        fts.upsert_note(parse_note(tmp_path / "a.md", tmp_path, HeadingChunker()))
        provider.texts.clear()
        result = rec.reconcile(store, ["a.md"], site="test")
        assert result.embedded == 1
        assert any("Quinces" in t for t in provider.texts)

    def test_legacy_rows_with_the_same_input_are_adopted_without_embedding(
        self, tmp_path
    ):
        _fts, provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        legacy = [
            DocumentRows(p, None, store.rows_for(p)) for p in sorted(store.tokens())
        ]
        store.publish_generation(store.identity, legacy)
        provider.texts.clear()
        result = rec.reconcile(store, site="test")
        assert (result.retokened, result.embedded, provider.texts) == (2, 0, [])
        assert None not in store.tokens().values()

    def test_a_body_less_document_loses_its_rows(self, tmp_path):
        fts, _, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        (tmp_path / "a.md").write_text("---\ntitle: A\n---\n")
        fts.upsert_note(parse_note(tmp_path / "a.md", tmp_path, HeadingChunker()))
        result = rec.reconcile(store, ["a.md"], site="test")
        assert result.removed == 1
        assert "a.md" not in metadata_by_path(store)

    def test_a_path_without_rows_is_removed_only_when_its_source_is_absent(
        self, tmp_path
    ):
        fts, _, store, rec = _setup(tmp_path, NOTES, probe=_Probe({"a.md": "absent"}))
        rec.reconcile(store, site="test")
        fts.delete_by_path("a.md")
        fts.delete_by_path("b.md")
        rec.reconcile(store, site="test")
        assert sorted(metadata_by_path(store)) == ["b.md"]


class TestFailures:
    def test_a_provider_failure_keeps_the_old_rows_and_the_pass_continues(
        self, tmp_path
    ):
        fts, provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        before = metadata_by_path(store)["a.md"]
        for name, body in (("a.md", "# A\n\nQuinces.\n"), ("b.md", "# B\n\nBarges.\n")):
            (tmp_path / name).write_text(body)
            fts.upsert_note(parse_note(tmp_path / name, tmp_path, HeadingChunker()))

        def flaky(texts: list[str]) -> list[list[float]]:
            if any("Quinces" in t for t in texts):
                raise RuntimeError("provider down")
            return MockEmbeddingProvider.embed(provider, texts)

        provider.embed = flaky  # type: ignore[method-assign]
        result = rec.reconcile(store, site="test")
        assert metadata_by_path(store)["a.md"] == before
        assert result.kept == 1
        assert any("Barges" in m["content"] for m in metadata_by_path(store)["b.md"])

    def test_a_refused_vector_keeps_the_old_rows(self, tmp_path):
        fts, provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        before = metadata_by_path(store)["a.md"]
        (tmp_path / "a.md").write_text("# A\n\nQuinces.\n")
        fts.upsert_note(parse_note(tmp_path / "a.md", tmp_path, HeadingChunker()))
        provider.embed = lambda texts: [[1.0] * 9 for _ in texts]  # type: ignore[method-assign]
        result = rec.reconcile(store, ["a.md"], site="test")
        assert (result.kept, metadata_by_path(store)["a.md"]) == (1, before)


class TestConflicts:
    def test_a_conflict_is_reported_and_the_other_documents_land(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        _fts, _, store, rec = _setup(tmp_path, NOTES)
        real = store.replace_document
        seen: set[str] = set()

        def once(document: DocumentRows, *, expected_token: Any) -> str:
            if document.path == "a.md" and "a.md" not in seen:
                seen.add("a.md")
                return "conflict"
            return real(document, expected_token=expected_token)

        monkeypatch.setattr(store, "replace_document", once)
        result = rec.reconcile(store, site="test")
        assert result.conflicts == {"a.md"}
        assert sorted(metadata_by_path(store)) == ["b.md"]
        assert rec.reconcile(store, ["a.md"], site="test").conflicts == set()
        assert sorted(metadata_by_path(store)) == ["a.md", "b.md"]

    def test_writes_pass_the_token_they_observed(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        fts, _, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        tokens_before = store.tokens()
        (tmp_path / "a.md").write_text("# A\n\nQuinces.\n")
        fts.upsert_note(parse_note(tmp_path / "a.md", tmp_path, HeadingChunker()))
        expected: list[Any] = []
        real = store.replace_document
        monkeypatch.setattr(
            store,
            "replace_document",
            lambda d, *, expected_token: (
                expected.append(expected_token)
                or real(d, expected_token=expected_token)
            ),
        )
        rec.reconcile(store, ["a.md"], site="test")
        assert expected == [tokens_before["a.md"]]
        assert ANY_TOKEN not in expected


class TestBulkRetoken:
    def test_many_adoptions_publish_one_generation(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        import markdown_vault_mcp.indexing.embedding_reconciler as module

        monkeypatch.setattr(module, "_BULK_RETOKEN", 2)
        _fts, _provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        store.publish_generation(
            store.identity,
            [DocumentRows(p, None, store.rows_for(p)) for p in sorted(store.tokens())],
        )
        generation = store.generation
        calls: list[str] = []
        real = store.replace_document
        monkeypatch.setattr(
            store,
            "replace_document",
            lambda d, *, expected_token: (
                calls.append(d.path) or real(d, expected_token=expected_token)
            ),
        )
        rec.reconcile(store, site="test")
        assert (calls, store.generation) == ([], generation + 1)
        assert None not in store.tokens().values()


class TestRebuild:
    def test_it_publishes_one_generation_with_tokens(self, tmp_path):
        fts, _, store, rec = _setup(tmp_path, NOTES)
        outcome = rec.rebuild(store, store.identity)
        assert (outcome.embedded, outcome.attempted, outcome.published) == (2, 2, True)
        hashes = {r["path"]: r["content_hash"] for r in fts.list_chunks()}
        assert (store.generation, store.tokens()) == (1, hashes)

    def test_a_failed_batch_is_left_for_convergence(self, tmp_path):
        notes = {f"n{i}.md": f"# N{i}\n\nBody {i}.\n" for i in range(6)}
        _fts, provider, store, rec = _setup(tmp_path, notes)
        real = provider.embed
        calls = 0

        def second_batch_fails(texts: list[str]) -> list[list[float]]:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("provider down")
            return real(texts)

        provider.embed = second_batch_fails  # type: ignore[method-assign]
        outcome = rec.rebuild(store, store.identity)
        assert (outcome.embedded, outcome.attempted, outcome.failed_batches) == (
            4,
            6,
            1,
        )
        provider.embed = real  # type: ignore[method-assign]
        provider.texts.clear()
        result = rec.reconcile(store, site="test")
        assert result.embedded == 2
        assert len(provider.texts) == 2


class TestBulkRetokenMemory:
    def test_the_bulk_publish_holds_vectors_as_float32(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        from array import array

        import markdown_vault_mcp.indexing.embedding_reconciler as module

        monkeypatch.setattr(module, "_BULK_RETOKEN", 2)
        _fts, _provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        store.publish_generation(
            store.identity,
            [DocumentRows(p, None, store.rows_for(p)) for p in sorted(store.tokens())],
        )
        published: list[DocumentRows] = []
        real = store.publish_generation

        def capture(identity, documents):  # type: ignore[no-untyped-def]
            docs = list(documents)
            published.extend(docs)
            return real(identity, docs)

        monkeypatch.setattr(store, "publish_generation", capture)
        rec.reconcile(store, site="test")
        vectors = [row.vector for doc in published for row in doc.rows]
        assert vectors
        assert all(isinstance(v, array) and v.typecode == "f" for v in vectors)


class TestRetries:
    def test_a_provider_failure_reports_the_path_for_a_retry(self, tmp_path):
        fts, provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        (tmp_path / "a.md").write_text("# A\n\nQuinces.\n")
        fts.upsert_note(parse_note(tmp_path / "a.md", tmp_path, HeadingChunker()))

        def down(_texts: list[str]) -> list[list[float]]:
            raise RuntimeError("provider down")

        provider.embed = down  # type: ignore[method-assign]
        assert rec.reconcile(store, site="test").retry == {"a.md"}


class TestRemovalRule:
    def _orphan(self, tmp_path, **kw):
        fts, _provider, store, rec = _setup(tmp_path, NOTES, **kw)
        rec.reconcile(store, site="test")
        fts.delete_by_path("a.md")
        return fts, store, rec

    def test_absent_removes(self, tmp_path):
        _, store, rec = self._orphan(tmp_path, probe=_Probe({"a.md": "absent"}))
        rec.reconcile(store, site="test")
        assert sorted(metadata_by_path(store)) == ["b.md"]

    def test_an_exclusion_removes_without_asking_the_source(self, tmp_path):
        probe = _Probe()
        _, store, rec = self._orphan(tmp_path, probe=probe, excluded=("a.md",))
        rec.reconcile(store, site="test")
        assert sorted(metadata_by_path(store)) == ["b.md"]
        assert probe.asked == []

    def test_a_tombstone_removes_without_asking_the_source(self, tmp_path):
        probe = _Probe()
        fts, store, rec = self._orphan(tmp_path, probe=probe)
        fts.upsert_tombstone(
            SkippedFile(path="a.md", category="parse_error", detail="x"),
            content_hash="h",
            modified_at=0.0,
        )
        rec.reconcile(store, site="test")
        assert sorted(metadata_by_path(store)) == ["b.md"]
        assert probe.asked == []

    def test_a_keyword_gap_keeps_and_says_so(
        self, tmp_path, caplog: pytest.LogCaptureFixture
    ):
        _, store, rec = self._orphan(tmp_path, probe=_Probe({"a.md": "present"}))
        with caplog.at_level(logging.WARNING):
            rec.reconcile(store, site="test")
        assert "a.md" in metadata_by_path(store)
        assert any(r.msg.startswith("embedding_reconcile_kept") for r in caplog.records)

    def test_unavailable_keeps_with_one_warning_per_pass(
        self, tmp_path, caplog: pytest.LogCaptureFixture
    ):
        fts, _provider, store, rec = _setup(
            tmp_path,
            NOTES,
            probe=_Probe({"a.md": "unavailable", "b.md": "unavailable"}),
        )
        rec.reconcile(store, site="test")
        fts.delete_by_path("a.md")
        fts.delete_by_path("b.md")
        with caplog.at_level(logging.WARNING):
            rec.reconcile(store, site="test")
        assert sorted(metadata_by_path(store)) == ["a.md", "b.md"]
        unavailable = [
            r
            for r in caplog.records
            if r.msg.startswith("embedding_reconcile_source_unavailable")
        ]
        assert len(unavailable) == 1


class _FailingWrites:
    """A store whose writes for one path fail as an I/O fault would."""

    def __init__(self, inner: Any, path: str) -> None:
        self._inner = inner
        self._path = path

    def replace_document(self, document: DocumentRows, *, expected_token: Any) -> str:
        if document.path == self._path:
            raise sqlite3.OperationalError("database or disk is full")
        return self._inner.replace_document(document, expected_token=expected_token)

    def remove_document(self, path: str, *, expected_token: Any) -> str:
        if path == self._path:
            raise sqlite3.OperationalError("database or disk is full")
        return self._inner.remove_document(path, expected_token=expected_token)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class TestStoreWriteFailures:
    def test_a_failed_write_is_retried_and_the_pass_continues(self, tmp_path, caplog):
        _fts, _provider, store, rec = _setup(tmp_path, NOTES)
        with caplog.at_level(logging.WARNING):
            result = rec.reconcile(_FailingWrites(store, "a.md"), site="test")
        assert result.retry == {"a.md"}
        assert set(store.tokens()) == {"b.md"}
        assert any(
            r.msg.startswith("embedding_reconcile_write_failed") for r in caplog.records
        )

    def test_a_failed_removal_is_retried(self, tmp_path):
        fts, _provider, store, rec = _setup(
            tmp_path, NOTES, probe=_Probe({"a.md": "absent"})
        )
        rec.reconcile(store, site="test")
        fts.delete_by_path("a.md")
        result = rec.reconcile(_FailingWrites(store, "a.md"), site="test")
        assert result.retry == {"a.md"}
        assert "a.md" in store.tokens()

    def test_a_read_only_store_still_raises(self, tmp_path):
        from markdown_vault_mcp.exceptions import ReadOnlyError

        _fts, _provider, store, rec = _setup(tmp_path, NOTES)

        class _ReadOnly(_FailingWrites):
            def replace_document(self, *_args: Any, **_kwargs: Any) -> str:
                raise ReadOnlyError("read-only")

        read_only = _ReadOnly(store, "a.md")
        with pytest.raises(ReadOnlyError):
            rec.reconcile(read_only, site="test")


class TestStoreWriteFailuresElsewhere:
    def test_a_failed_retoken_is_retried(self, tmp_path):
        fts, _provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        (tmp_path / "a.md").write_text(
            "---\nmodified: 2026-10-17\n---\n# A\n\nApples.\n"
        )
        fts.upsert_note(parse_note(tmp_path / "a.md", tmp_path, HeadingChunker()))
        result = rec.reconcile(_FailingWrites(store, "a.md"), ["a.md"], site="test")
        assert (result.retry, result.retokened) == ({"a.md"}, 0)

    def test_a_read_only_store_raises_from_a_removal(self, tmp_path):
        from markdown_vault_mcp.exceptions import ReadOnlyError

        fts, _provider, store, rec = _setup(
            tmp_path, NOTES, probe=_Probe({"a.md": "absent"})
        )
        rec.reconcile(store, site="test")
        fts.delete_by_path("a.md")

        class _ReadOnlyRemoval(_FailingWrites):
            def remove_document(self, *_args: Any, **_kwargs: Any) -> str:
                raise ReadOnlyError("read-only")

        read_only = _ReadOnlyRemoval(store, "b.md")
        with pytest.raises(ReadOnlyError):
            rec.reconcile(read_only, site="test")


class _Conflicting(_FailingWrites):
    """A store whose replace for one path reports another writer got there."""

    def replace_document(self, document: DocumentRows, *, expected_token: Any) -> str:
        if document.path == self._path:
            return "conflict"
        return self._inner.replace_document(document, expected_token=expected_token)


class TestRetokenOutcomes:
    def _retokenable(self, tmp_path):
        fts, _provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        (tmp_path / "a.md").write_text(
            "---\nmodified: 2026-10-17\n---\n# A\n\nApples.\n"
        )
        fts.upsert_note(parse_note(tmp_path / "a.md", tmp_path, HeadingChunker()))
        return store, rec

    def test_a_retoken_conflict_is_requeued(self, tmp_path):
        store, rec = self._retokenable(tmp_path)
        result = rec.reconcile(_Conflicting(store, "a.md"), ["a.md"], site="test")
        assert (result.conflicts, result.retokened) == ({"a.md"}, 0)

    def test_a_read_only_store_raises_from_a_retoken(self, tmp_path):
        from markdown_vault_mcp.exceptions import ReadOnlyError

        store, rec = self._retokenable(tmp_path)

        class _ReadOnly(_FailingWrites):
            def replace_document(self, *_args: Any, **_kwargs: Any) -> str:
                raise ReadOnlyError("read-only")

        read_only = _ReadOnly(store, "a.md")
        with pytest.raises(ReadOnlyError):
            rec.reconcile(read_only, ["a.md"], site="test")


class TestBulkRetokenWriteFailures:
    def _legacy(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        import markdown_vault_mcp.indexing.embedding_reconciler as module

        monkeypatch.setattr(module, "_BULK_RETOKEN", 2)
        _fts, _provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        store.publish_generation(
            store.identity,
            [DocumentRows(p, None, store.rows_for(p)) for p in sorted(store.tokens())],
        )
        return store, rec

    def test_a_failed_bulk_publish_retries_every_path(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        store, rec = self._legacy(tmp_path, monkeypatch)

        def refuse(*_args: Any, **_kwargs: Any) -> None:
            raise sqlite3.OperationalError("database or disk is full")

        monkeypatch.setattr(store, "publish_generation", refuse)
        with caplog.at_level(logging.WARNING):
            result = rec.reconcile(store, site="test")
        assert (result.retry, result.retokened) == ({"a.md", "b.md"}, 0)
        assert set(store.tokens().values()) == {None}
        assert [
            r
            for r in caplog.records
            if r.msg.startswith("embedding_reconcile_bulk_write_failed")
        ]

    def test_a_read_only_store_raises_from_a_bulk_publish(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        from markdown_vault_mcp.exceptions import ReadOnlyError

        store, rec = self._legacy(tmp_path, monkeypatch)

        def read_only(*_args: Any, **_kwargs: Any) -> None:
            raise ReadOnlyError("read-only")

        monkeypatch.setattr(store, "publish_generation", read_only)
        with pytest.raises(ReadOnlyError):
            rec.reconcile(store, site="test")


SECTIONS = HeadingChunker(short_doc_lines=0)
SECTIONED = {
    "s.md": "# S\n\nIntro.\n\n## One\n\nApples.\n\n## Two\n\nBoats.\n\n## End\n\nFin.\n"
}
EDITED = SECTIONED["s.md"].replace("Boats", "Barges")


def _edit(fts: FTSIndex, tmp_path: Path, name: str, text: str) -> None:
    (tmp_path / name).write_text(text)
    fts.upsert_note(parse_note(tmp_path / name, tmp_path, SECTIONS))


def _vectors(store: Any, path: str) -> dict[str, np.ndarray]:
    return {
        r.metadata["content"]: np.asarray(r.vector, dtype=np.float32)
        for r in store.rows_for(path)
    }


class TestChunkReuse:
    def test_only_the_changed_chunk_is_embedded(self, tmp_path):
        fts, provider, store, rec = _setup(tmp_path, SECTIONED, chunker=SECTIONS)
        rec.reconcile(store, site="test")
        _edit(fts, tmp_path, "s.md", EDITED)
        provider.texts.clear()
        result = rec.reconcile(store, ["s.md"], site="test")
        assert (result.embedded, result.reused) == (1, 3)
        assert len(provider.texts) == 1
        assert "Barges" in provider.texts[0]
        token = fts.list_chunks(paths=["s.md"])[0]["content_hash"]
        assert store.tokens()["s.md"] == token

    def test_moved_sections_embed_nothing_and_their_rows_move(self, tmp_path):
        fts, provider, store, rec = _setup(tmp_path, SECTIONED, chunker=SECTIONS)
        rec.reconcile(store, site="test")
        swapped = (
            "# S\n\nIntro.\n\n## Two\n\nBoats.\n\n## One\n\nApples.\n\n## End\n\nFin.\n"
        )
        _edit(fts, tmp_path, "s.md", swapped)
        provider.texts.clear()
        result = rec.reconcile(store, ["s.md"], site="test")
        assert (result.embedded, result.reused, provider.texts) == (0, 4, [])
        stored = sorted(
            (r.metadata["heading"], r.metadata["start_line"])
            for r in store.rows_for("s.md")
        )
        indexed = sorted(
            (r["heading"], r["start_line"]) for r in fts.list_chunks(paths=["s.md"])
        )
        assert stored == indexed

    def test_a_reused_vector_equals_the_stored_one(self, tmp_path):
        fts, provider, store, rec = _setup(tmp_path, SECTIONED, chunker=SECTIONS)
        rec.reconcile(store, site="test")
        before = _vectors(store, "s.md")
        _edit(fts, tmp_path, "s.md", EDITED)
        real = provider.embed
        # A provider that no longer reproduces the stored vectors: only a
        # chunk that was not sent to it can still carry its stored vector.
        provider.embed = lambda texts: real([f"salt {t}" for t in texts])  # type: ignore[method-assign]
        rec.reconcile(store, ["s.md"], site="test")
        after = _vectors(store, "s.md")
        kept = [content for content in before if content in after]
        assert len(kept) == 3
        for content in kept:
            np.testing.assert_array_max_ulp(after[content], before[content], maxulp=1)

    def test_a_repeated_new_input_is_embedded_once(self, tmp_path):
        fts, provider, store, rec = _setup(tmp_path, SECTIONED, chunker=SECTIONS)
        rec.reconcile(store, site="test")
        twice = SECTIONED["s.md"].replace("Apples", "Carts").replace("Boats", "Carts")
        _edit(fts, tmp_path, "s.md", twice.replace("## Two", "## One"))
        provider.texts.clear()
        result = rec.reconcile(store, ["s.md"], site="test")
        assert (result.embedded, result.reused) == (2, 2)
        assert len(provider.texts) == 1

    def test_a_provider_failure_on_the_changed_chunk_keeps_every_row(self, tmp_path):
        fts, provider, store, rec = _setup(tmp_path, SECTIONED, chunker=SECTIONS)
        rec.reconcile(store, site="test")
        before = metadata_by_path(store)["s.md"]
        _edit(fts, tmp_path, "s.md", EDITED)

        def down(_texts: list[str]) -> list[list[float]]:
            raise RuntimeError("provider down")

        provider.embed = down  # type: ignore[method-assign]
        result = rec.reconcile(store, ["s.md"], site="test")
        assert (result.kept, result.reused, result.retry) == (4, 0, {"s.md"})
        assert metadata_by_path(store)["s.md"] == before

    def test_the_first_chunk_preamble_is_part_of_the_reused_input(self, tmp_path):
        builder = EmbedTextBuilder(searchable_fields=("summary",))
        note = "---\nsummary: Fruit\n---\n# S\n\nIntro.\n\n## One\n\nApples.\n"
        fts, provider, store, rec = _setup(
            tmp_path, {"s.md": note}, chunker=SECTIONS, builder=builder
        )
        rec.reconcile(store, site="test")
        pears = note.replace("Apples", "Pears")
        _edit(fts, tmp_path, "s.md", pears)
        provider.texts.clear()
        result = rec.reconcile(store, ["s.md"], site="test")
        assert (result.embedded, result.reused) == (1, 1)
        assert "Pears" in provider.texts[0]
        _edit(fts, tmp_path, "s.md", pears.replace("Fruit", "Food"))
        provider.texts.clear()
        result = rec.reconcile(store, ["s.md"], site="test")
        assert (result.embedded, result.reused) == (1, 1)
        assert len(provider.texts) == 1
        assert "Food" in provider.texts[0]

    def test_a_row_without_its_input_metadata_is_embedded(self, tmp_path):
        _fts, provider, store, rec = _setup(tmp_path, SECTIONED, chunker=SECTIONS)
        rec.reconcile(store, site="test")
        stripped = [
            VectorRow(r.vector, {k: v for k, v in r.metadata.items() if k != "title"})
            for r in store.rows_for("s.md")
        ]
        store.publish_generation(store.identity, [DocumentRows("s.md", None, stripped)])
        provider.texts.clear()
        result = rec.reconcile(store, ["s.md"], site="test")
        assert (result.embedded, result.reused, len(provider.texts)) == (4, 0, 4)


class TestStoredInputs:
    """One matching rule for own-store reuse and donors (#1505)."""

    def test_stored_inputs_is_the_reconcilers_rule(self) -> None:
        from markdown_vault_mcp.indexing.embedding_reconciler import stored_inputs

        builder = EmbedTextBuilder(searchable_fields=("summary",))
        rows = [
            VectorRow(
                [1.0, 0.0],
                {"title": "T", "heading": "H", "content": "c1", "preamble": "p"},
            ),
            VectorRow([0.0, 1.0], {"title": "T", "content": "c2"}),
            VectorRow([1.0, 1.0], {"content": "no title"}),
        ]
        known = stored_inputs(builder, rows)
        first = builder.build(
            title="T", heading="H", content="c1", fields_text="p", is_first_chunk=True
        )
        second = builder.build(
            title="T", heading=None, content="c2", fields_text="", is_first_chunk=False
        )
        assert set(known) == {first, second}
        assert list(known[first]) == [1.0, 0.0]


class _FakeDonor:
    """A donor answering from a dict of path -> {input: vector}."""

    def __init__(self, given: dict[str, dict[str, Any]], *, fail: bool = False) -> None:
        self.given, self.fail = given, fail
        self.source = "fake:donor"
        self.refused_rows = 0

    def vectors_for(self, path: str) -> dict[str, Any]:
        if self.fail:
            from markdown_vault_mcp.exceptions import StoreUnavailableError

            raise StoreUnavailableError("gone", backend="fake", location="donor")
        return self.given.get(path, {})


def _donate(rec: EmbeddingReconciler, paths: list[str], dim: int = 8) -> _FakeDonor:
    """A donor holding a distinct unit vector for every input of *paths*."""
    docs = rec.documents()
    given: dict[str, dict[str, Any]] = {}
    for n, path in enumerate(paths):
        for i, text in enumerate(docs[path].texts):
            vector = np.zeros(dim, dtype=np.float32)
            vector[(n + i) % dim] = 1.0
            given.setdefault(path, {})[text] = vector
    return _FakeDonor(given)


def _failing_second_batch(provider: _Counting) -> None:
    real = provider.embed
    calls = 0

    def embed(texts: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("provider down")
        return real(texts)

    provider.embed = embed  # type: ignore[method-assign]


class TestRebuildWithDonor:
    """#1505 child 4: the full build copies donor vectors."""

    def test_a_donor_supplies_matching_chunks(self, tmp_path):
        _fts, provider, store, rec = _setup(tmp_path, NOTES)
        donated, other = sorted(rec.documents())
        donor = _donate(rec, [donated])
        outcome = rec.rebuild(store, store.identity, donor=donor)
        docs = rec.documents()
        assert provider.texts == list(docs[other].texts)
        assert outcome.reused == len(docs[donated].texts)
        for row, text in zip(store.rows_for(donated), docs[donated].texts, strict=True):
            assert np.allclose(row.vector, donor.given[donated][text])

    def test_a_changed_chunk_is_embedded_and_the_rest_donated(self, tmp_path):
        notes = {"a.md": "# A\n\n## One\n\nApples.\n\n## Two\n\nPears.\n"}
        _fts, provider, store, rec = _setup(tmp_path, notes)
        donor = _donate(rec, ["a.md"])
        first = rec.documents()["a.md"].texts[0]
        del donor.given["a.md"][first]
        outcome = rec.rebuild(store, store.identity, donor=donor)
        assert provider.texts == [first]
        assert (outcome.embedded, outcome.reused) == (
            1,
            len(rec.documents()["a.md"].texts) - 1,
        )

    def test_reuse_own_rows_when_asked(self, tmp_path):
        _fts, provider, store, rec = _setup(tmp_path, NOTES)
        rec.rebuild(store, store.identity)
        provider.texts.clear()
        outcome = rec.rebuild(store, store.identity, reuse_own=True)
        assert (provider.texts, outcome.embedded, outcome.published) == ([], 0, True)

    def test_a_forced_rebuild_still_embeds_every_chunk(self, tmp_path):
        _fts, provider, store, rec = _setup(tmp_path, NOTES)
        rec.rebuild(store, store.identity)
        provider.texts.clear()
        outcome = rec.rebuild(store, store.identity)
        assert outcome.embedded == outcome.attempted == len(provider.texts) > 0
        assert outcome.reused == 0

    def test_a_failed_batch_keeps_a_populated_store(self, tmp_path, caplog):
        notes = {f"n{i}.md": f"# N{i}\n\nBody {i}.\n" for i in range(6)}
        _fts, provider, store, rec = _setup(tmp_path, notes)
        rec.rebuild(store, store.identity)
        expected = (store.generation, store.tokens(), store.count)
        _failing_second_batch(provider)
        with caplog.at_level(logging.WARNING):
            outcome = rec.rebuild(store, store.identity)
        actual = (store.generation, store.tokens(), store.count)
        assert actual == expected
        assert (outcome.published, outcome.failed_batches) == (False, 1)
        assert [
            r.args
            for r in caplog.records
            if r.msg.startswith("build_embeddings_not_published ")
        ] == [(1,)]

    def test_a_failed_batch_into_an_empty_store_publishes_the_rest(self, tmp_path):
        notes = {f"n{i}.md": f"# N{i}\n\nBody {i}.\n" for i in range(6)}
        _fts, provider, store, rec = _setup(tmp_path, notes)
        _failing_second_batch(provider)
        outcome = rec.rebuild(store, store.identity)
        assert (outcome.published, outcome.embedded, store.count) == (True, 4, 4)

    def test_a_donor_vector_of_another_length_is_embedded(self, tmp_path):
        _fts, provider, store, rec = _setup(tmp_path, NOTES)
        path = sorted(rec.documents())[0]
        donor = _donate(rec, [path], dim=3)
        outcome = rec.rebuild(store, store.identity, donor=donor)
        assert outcome.reused == 0
        assert set(rec.documents()[path].texts) <= set(provider.texts)
        assert all(len(r.vector) == 8 for r in store.rows_for(path))

    def test_a_donor_read_failure_publishes_nothing(self, tmp_path):
        from markdown_vault_mcp.exceptions import StoreUnavailableError

        _fts, _provider, store, rec = _setup(tmp_path, NOTES)
        with pytest.raises(StoreUnavailableError):
            rec.rebuild(store, store.identity, donor=_FakeDonor({}, fail=True))
        assert (store.generation, store.count) == (0, 0)

    def test_the_donor_summary_is_logged_once(self, tmp_path, caplog):
        _fts, _provider, store, rec = _setup(tmp_path, NOTES)
        donated, _other = sorted(rec.documents())
        donor = _donate(rec, [donated])
        donor.refused_rows = 2
        with caplog.at_level(logging.INFO):
            outcome = rec.rebuild(store, store.identity, donor=donor)
        lines = [
            r.args for r in caplog.records if r.msg.startswith("embeddings_donor_used ")
        ]
        assert lines == [("fake:donor", outcome.reused, outcome.embedded, 2)]

    def test_a_real_donor_store_feeds_the_build(self, tmp_path):
        from markdown_vault_mcp.indexing.donor import Donor

        _fts, provider, store, rec = _setup(tmp_path, NOTES)
        base = tmp_path / "donor-embeddings"
        source = NumpySidecarBackend().open(base, store.identity)
        rec.rebuild(source, store.identity)
        source.close()
        provider.texts.clear()
        builder = EmbedTextBuilder()
        donor = Donor.open(NumpySidecarBackend(), base, store.identity, builder=builder)
        assert isinstance(donor, Donor)
        with donor:
            outcome = rec.rebuild(store, store.identity, donor=donor)
        assert (provider.texts, outcome.embedded, outcome.published) == ([], 0, True)
        assert outcome.reused == outcome.attempted > 0
