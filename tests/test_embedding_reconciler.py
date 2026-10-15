"""The one writer of embedding rows (#1768)."""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Any

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
)
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.vectors import metadata_by_path

if TYPE_CHECKING:
    from pathlib import Path


class _Counting(MockEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__(dim=8)
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return super().embed(texts)


def _index(tmp_path: Path, notes: dict[str, str]) -> FTSIndex:
    fts = FTSIndex(db_path=":memory:")
    for name, text in notes.items():
        (tmp_path / name).write_text(text)
        fts.upsert_note(parse_note(tmp_path / name, tmp_path, HeadingChunker()))
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
):
    fts = _index(tmp_path, notes)
    provider = _Counting()
    store = NumpySidecarBackend().open(None, EmbeddingIdentity.of(provider, "v1"))
    reconciler = EmbeddingReconciler(
        fts=fts,
        provider=provider,
        embed_builder=EmbedTextBuilder(),
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
        assert rec.rebuild(store, store.identity) == (2, 2)
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
        embedded, attempted = rec.rebuild(store, store.identity)
        assert (embedded, attempted) == (4, 6)
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

        with pytest.raises(ReadOnlyError):
            rec.reconcile(_ReadOnly(store, "a.md"), site="test")
