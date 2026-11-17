"""The one writer of embedding rows (#1768)."""

from __future__ import annotations

import logging
import sqlite3
from array import array
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

import markdown_vault_mcp.indexing.embedding_reconciler as reconciler_module
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
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        self.calls.append(list(texts))
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


def test_changed_documents_share_one_request(tmp_path):
    fts, provider, store, rec = _setup(tmp_path, NOTES)
    rec.reconcile(store, site="test")
    for path, body in (("a.md", "Quinces"), ("b.md", "Harbours")):
        (tmp_path / path).write_text(f"# {path[0].upper()}\n\n{body}.\n")
        fts.upsert_note(parse_note(tmp_path / path, tmp_path, HeadingChunker()))
    provider.calls.clear()
    result = rec.reconcile(store, ["a.md", "b.md"], site="flush")
    assert len(provider.calls) == 1
    assert len(provider.calls[0]) == 2
    assert result.embedded == 2
    for path, text in zip(("a.md", "b.md"), provider.calls[0], strict=True):
        row = store.rows_for(path)[0]
        assert row.metadata["path"] == path
        vector = np.array(MockEmbeddingProvider.embed(provider, [text])[0])
        assert list(row.vector) == pytest.approx(vector / np.linalg.norm(vector))


def _five_chunk_notes(tmp_path, sibling=False):
    body = "# A\n\n" + "\n".join(f"## Section {i}\n\nBody {i}.\n" for i in range(5))
    notes = {"a.md": body}
    if sibling:
        notes["b.md"] = "# B\n\nOther.\n"
    chunker = HeadingChunker(short_doc_lines=0)
    fts, provider, store, rec = _setup(tmp_path, notes, chunker=chunker)
    rec.reconcile(store, site="test")
    old = store.rows_for("a.md")
    for path, text in notes.items():
        (tmp_path / path).write_text(
            text.replace("Body", "Changed").replace("Other", "New other")
        )
        fts.upsert_note(parse_note(tmp_path / path, tmp_path, chunker))
    provider.calls.clear()
    return provider, store, rec, old


def test_lone_split_request_failure_is_not_replayed(tmp_path, monkeypatch):
    provider, store, rec, old = _five_chunk_notes(tmp_path)

    def fail(texts):
        provider.calls.append(list(texts))
        raise RuntimeError("down")

    monkeypatch.setattr(provider, "embed", fail)
    result = rec.reconcile(store, site="flush")
    assert [len(call) for call in provider.calls] == [4]
    assert result.retry == {"a.md"}
    assert result.kept == 5
    assert store.rows_for("a.md") == old


def test_lone_partial_wrong_count_falls_back_to_store_policy(tmp_path, monkeypatch):
    provider, store, rec, _ = _five_chunk_notes(tmp_path)
    embed = provider.embed

    def malformed_once(texts):
        if not provider.calls:
            provider.calls.append(list(texts))
            return []
        return embed(texts)

    monkeypatch.setattr(provider, "embed", malformed_once)
    result = rec.reconcile(store, site="flush")
    assert [len(call) for call in provider.calls] == [4, 4, 1]
    assert not result.retry
    assert all("Changed" in row.metadata["content"] for row in store.rows_for("a.md"))


def test_lone_tail_failure_is_not_replayed(tmp_path, monkeypatch):
    provider, store, rec, old = _five_chunk_notes(tmp_path)
    embed = provider.embed

    def fail_tail(texts):
        if len(texts) == 1:
            provider.calls.append(list(texts))
            raise RuntimeError("down")
        return embed(texts)

    monkeypatch.setattr(provider, "embed", fail_tail)
    result = rec.reconcile(store, site="flush")
    assert [len(call) for call in provider.calls] == [4, 1]
    assert result.retry == {"a.md"}
    assert store.rows_for("a.md") == old


def test_shared_outage_retries_each_document_once(tmp_path, monkeypatch):
    _, provider, store, rec = _setup(tmp_path, NOTES)

    def fail(texts):
        provider.calls.append(list(texts))
        raise RuntimeError("down")

    monkeypatch.setattr(provider, "embed", fail)
    result = rec.reconcile(store, site="flush")
    assert [len(call) for call in provider.calls] == [2, 1, 1]
    assert result.retry == {"a.md", "b.md"}
    assert not store.tokens()


def test_shared_failure_retries_only_unembedded_suffix(tmp_path, monkeypatch):
    provider, store, rec, old = _five_chunk_notes(tmp_path, sibling=True)
    embed = provider.embed

    def fail_tail(texts):
        if any("Changed 4" in text for text in texts):
            provider.calls.append(list(texts))
            raise RuntimeError("rejected")
        return embed(texts)

    monkeypatch.setattr(provider, "embed", fail_tail)
    result = rec.reconcile(store, site="flush")
    assert [len(call) for call in provider.calls] == [4, 2, 1, 1]
    assert result.retry == {"a.md"}
    assert store.rows_for("a.md") == old
    assert "New other" in store.rows_for("b.md")[0].metadata["content"]
    monkeypatch.setattr(provider, "embed", embed)
    provider.calls.clear()
    recovered = rec.reconcile(store, site="flush")
    assert not recovered.retry
    assert all("Changed" in row.metadata["content"] for row in store.rows_for("a.md"))


def test_multi_chunk_documents_fill_shared_requests(tmp_path):
    notes = {
        f"{i}.md": f"# Note {i}\n\nIntro {i}.\n\n## One\n\nApple {i}.\n\n## Two\n\nBoat {i}.\n"
        for i in range(4)
    }
    _, provider, store, rec = _setup(
        tmp_path, notes, chunker=HeadingChunker(short_doc_lines=0)
    )
    result = rec.reconcile(store, site="flush")
    assert result.embedded == 12
    assert all(len(store.rows_for(path)) == 3 for path in notes)
    assert [len(call) for call in provider.calls] == [4, 4, 4]


def test_batch_limit_spans_documents(tmp_path):
    notes = {f"{i}.md": f"# Note {i}\n\nBody {i}.\n" for i in range(5)}
    _, provider, store, rec = _setup(tmp_path, notes)
    rec.reconcile(store, site="flush")
    assert [len(call) for call in provider.calls] == [4, 1]
    assert set(store.tokens()) == set(notes)


def test_oversized_document_tail_shares_next_request(tmp_path):
    notes = {
        "a.md": "# A\n\n"
        + "\n".join(f"## Section {i}\n\nContent {i}.\n" for i in range(5)),
        "b.md": "# B\n\nOther.\n",
    }
    _, provider, store, rec = _setup(
        tmp_path, notes, chunker=HeadingChunker(short_doc_lines=0)
    )
    rec.reconcile(store, site="flush")
    assert [len(call) for call in provider.calls] == [4, 2]
    assert "Other" in provider.calls[-1][1]
    assert len(store.rows_for("a.md")) == 5


def test_failure_across_document_boundary_keeps_whole_failed_document(
    tmp_path, monkeypatch
):
    chunker = HeadingChunker(short_doc_lines=0)
    notes = {
        f"{c}.md": f"# {c}\n\nIntro {c}.\n\n## One\n\nApple {c}.\n\n## Two\n\nBoat {c}.\n"
        for c in "abc"
    }
    fts, provider, store, rec = _setup(tmp_path, notes, chunker=chunker)
    rec.reconcile(store, site="test")
    old = store.rows_for("b.md")
    for path, body in notes.items():
        (tmp_path / path).write_text(
            body.replace("Intro", "Changed Intro")
            .replace("Apple", "Changed Apple")
            .replace("Boat", "Changed Boat")
        )
        fts.upsert_note(parse_note(tmp_path / path, tmp_path, chunker))
    provider.calls.clear()
    embed = provider.embed

    def reject(texts):
        if any("Changed Apple b" in text for text in texts):
            provider.calls.append(list(texts))
            raise RuntimeError("rejected b")
        return embed(texts)

    monkeypatch.setattr(provider, "embed", reject)
    result = rec.reconcile(store, site="flush")
    assert result.retry == {"b.md"}
    assert store.rows_for("b.md") == old
    assert all(
        "Changed" in row.metadata["content"]
        for p in ("a.md", "c.md")
        for row in store.rows_for(p)
    )
    assert any(
        len(call) == 4 and any("Changed Apple b" in text for text in call)
        for call in provider.calls
    )


def test_cross_boundary_batch_preserves_conditional_conflict(tmp_path, monkeypatch):
    chunker = HeadingChunker(short_doc_lines=0)
    notes = {
        f"{c}.md": f"# {c}\n\nIntro {c}.\n\n## One\n\nApple {c}.\n\n## Two\n\nBoat {c}.\n"
        for c in "ab"
    }
    fts, provider, store, rec = _setup(tmp_path, notes, chunker=chunker)
    rec.reconcile(store, site="test")
    for path, body in notes.items():
        (tmp_path / path).write_text(
            body.replace("Intro", "New Intro")
            .replace("Apple", "New Apple")
            .replace("Boat", "New Boat")
        )
        fts.upsert_note(parse_note(tmp_path / path, tmp_path, chunker))
    embed = provider.embed
    changed = []

    def concurrent(texts):
        if not changed:
            rows = store.rows_for("b.md")
            store.replace_document(
                DocumentRows("b.md", "other", rows),
                expected_token=store.tokens()["b.md"],
            )
            changed.append(True)
        return embed(texts)

    monkeypatch.setattr(provider, "embed", concurrent)
    result = rec.reconcile(store, site="flush")
    assert result.conflicts == {"b.md"}
    assert store.tokens()["b.md"] == "other"
    assert all("New" in row.metadata["content"] for row in store.rows_for("a.md"))


@pytest.mark.parametrize("failure", ["rejected", "count"])
def test_shared_request_falls_back_per_document(tmp_path, monkeypatch, failure):
    fts, provider, store, rec = _setup(tmp_path, NOTES)
    rec.reconcile(store, site="test")
    old = store.rows_for("a.md")
    for path in NOTES:
        (tmp_path / path).write_text(f"# {path[0].upper()}\n\nNew {path}.\n")
        fts.upsert_note(parse_note(tmp_path / path, tmp_path, HeadingChunker()))
    provider.calls.clear()
    embed = provider.embed

    def flaky(texts):
        if failure == "rejected" and any("New a.md" in t for t in texts):
            provider.calls.append(list(texts))
            raise RuntimeError("rejected")
        if failure == "count" and len(texts) > 1:
            provider.calls.append(list(texts))
            return []
        return embed(texts)

    monkeypatch.setattr(provider, "embed", flaky)
    result = rec.reconcile(store, site="flush")
    assert [len(call) for call in provider.calls] == [2, 1, 1]
    assert "New b.md" in store.rows_for("b.md")[0].metadata["content"]
    if failure == "rejected":
        assert result.retry == {"a.md"}
        assert store.rows_for("a.md") == old
    else:
        assert not result.retry
        assert "New a.md" in store.rows_for("a.md")[0].metadata["content"]


def test_reuse_only_document_publishes_while_other_is_pending(tmp_path):
    notes = {
        "a.md": "# A\n\nApple.\n",
        "b.md": "# S\n\nIntro.\n\n## One\n\nApples.\n\n## Two\n\nBoats.\n\n## End\n\nFin.\n",
    }
    chunker = HeadingChunker(short_doc_lines=0)
    fts, provider, store, rec = _setup(tmp_path, notes, chunker=chunker)
    rec.reconcile(store, site="test")
    (tmp_path / "a.md").write_text("# A\n\nNew apples.\n")
    (tmp_path / "b.md").write_text(
        "# S\n\nIntro.\n\n## Two\n\nBoats.\n\n## One\n\nApples.\n\n## End\n\nFin.\n"
    )
    for path in notes:
        fts.upsert_note(parse_note(tmp_path / path, tmp_path, chunker))
    provider.calls.clear()
    result = rec.reconcile(store, site="flush")
    assert [len(call) for call in provider.calls] == [1]
    assert result.reused == 4
    assert [r.metadata["heading"] for r in store.rows_for("b.md")] == [
        "S",
        "Two",
        "One",
        "End",
    ]


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


class _BatchedMetadata:
    """A store that reads row metadata in batches, counting its reads (#1937)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.batches: list[list[str]] = []
        self.rows_read: list[str] = []

    def metadata_for(self, paths: Any) -> dict[str, list[dict[str, Any]]]:
        self.batches.append(list(paths))
        found = {p: [dict(r.metadata) for r in self._inner.rows_for(p)] for p in paths}
        return {p: rows for p, rows in found.items() if rows}

    def rows_for(self, path: str) -> list[VectorRow]:
        self.rows_read.append(path)
        return self._inner.rows_for(path)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class TestBatchedMetadata:
    def test_a_converged_pass_reads_metadata_and_no_vectors(self, tmp_path):
        _, _provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        batched = _BatchedMetadata(store)
        result = rec.reconcile(batched, site="test")
        assert result.up_to_date == 2
        assert batched.batches == [["a.md", "b.md"]]
        assert batched.rows_read == []

    def test_documents_are_read_in_batches(self, tmp_path, monkeypatch):
        notes = {**NOTES, "c.md": "# C\n\nCherries.\n"}
        _, _provider, store, rec = _setup(tmp_path, notes)
        rec.reconcile(store, site="test")
        monkeypatch.setattr(
            "markdown_vault_mcp.indexing.embedding_reconciler._METADATA_BATCH", 2
        )
        batched = _BatchedMetadata(store)
        assert rec.reconcile(batched, site="test").up_to_date == 3
        assert batched.batches == [["a.md", "b.md"], ["c.md"]]

    def test_only_a_changed_document_reads_its_vectors(self, tmp_path):
        fts, _provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        (tmp_path / "a.md").write_text("# A\n\nQuinces.\n")
        fts.upsert_note(parse_note(tmp_path / "a.md", tmp_path, HeadingChunker()))
        batched = _BatchedMetadata(store)
        result = rec.reconcile(batched, site="test")
        assert (result.embedded, result.up_to_date) == (1, 1)
        assert batched.rows_read == ["a.md"]

    def test_a_body_less_document_is_removed_without_reading_its_rows(self, tmp_path):
        fts, _provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        (tmp_path / "a.md").write_text("---\ntitle: A\n---\n")
        fts.upsert_note(parse_note(tmp_path / "a.md", tmp_path, HeadingChunker()))
        batched = _BatchedMetadata(store)
        result = rec.reconcile(batched, site="test")
        assert (result.removed, result.up_to_date) == (1, 1)
        assert "a.md" not in metadata_by_path(store)
        assert batched.rows_read == []

    def test_a_moved_token_is_retokened_from_a_batch(self, tmp_path):
        fts, provider, store, rec = _setup(tmp_path, NOTES)
        rec.reconcile(store, site="test")
        (tmp_path / "a.md").write_text(
            "---\nmodified: 2026-10-12\n---\n# A\n\nApples.\n"
        )
        fts.upsert_note(parse_note(tmp_path / "a.md", tmp_path, HeadingChunker()))
        provider.texts.clear()
        batched = _BatchedMetadata(store)
        result = rec.reconcile(batched, site="test")
        assert (result.retokened, result.up_to_date, provider.texts) == (1, 1, [])
        assert batched.batches == [["a.md", "b.md"]]
        assert batched.rows_read == ["a.md"]

    def test_a_new_document_reads_no_rows(self, tmp_path):
        _, _provider, store, rec = _setup(tmp_path, NOTES)
        batched = _BatchedMetadata(store)
        assert rec.reconcile(batched, site="test").embedded == 2
        assert batched.rows_read == []


class _BatchedRows(_BatchedMetadata):
    """A store that also reads many documents' rows at once, as PostgreSQL does (#1970)."""

    def __init__(self, inner: Any) -> None:
        super().__init__(inner)
        self.row_batches: list[list[str]] = []

    def rows_by_path(self, paths: Any) -> dict[str, list[VectorRow]]:
        self.row_batches.append(list(paths))
        found = {
            p: [
                VectorRow(array("f", r.vector), r.metadata)
                for r in self._inner.rows_for(p)
            ]
            for p in paths
        }
        return {p: rows for p, rows in found.items() if rows}


THREE = {**NOTES, "c.md": "# C\n\nCherries.\n"}


class TestBatchedRows:
    def _untokened(self, tmp_path: Path) -> tuple[Any, EmbeddingReconciler]:
        _fts, _provider, store, rec = _setup(tmp_path, THREE)
        rec.reconcile(store, site="test")
        store.publish_generation(
            store.identity,
            [DocumentRows(p, None, store.rows_for(p)) for p in sorted(store.tokens())],
        )
        return store, rec

    def test_a_bulk_retoken_reads_rows_in_batches(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(reconciler_module, "_BULK_RETOKEN", 2)
        monkeypatch.setattr(reconciler_module, "_ROWS_BATCH", 2)
        store, rec = self._untokened(tmp_path)
        before = {p: store.rows_for(p) for p in store.tokens()}
        batched = _BatchedRows(store)
        result = rec.reconcile(batched, site="test")
        assert result.retokened == 3
        assert batched.rows_read == []
        assert [len(b) for b in batched.row_batches] == [2, 1]
        assert sorted(p for b in batched.row_batches for p in b) == sorted(THREE)
        assert store.tokens() == {p: d.token for p, d in rec.documents().items()}
        for path, rows in before.items():
            assert np.allclose(
                [r.vector for r in store.rows_for(path)], [r.vector for r in rows]
            )

    def test_a_rebuild_reusing_its_own_rows_reads_them_in_batches(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(reconciler_module, "_ROWS_BATCH", 2)
        _fts, provider, store, rec = _setup(tmp_path, THREE)
        rec.rebuild(store, store.identity)
        provider.texts.clear()
        batched = _BatchedRows(store)
        outcome = rec.rebuild(batched, store.identity, reuse_own=True)
        assert (provider.texts, outcome.embedded, outcome.published) == ([], 0, True)
        assert batched.rows_read == []
        assert batched.row_batches == [["a.md", "b.md"], ["c.md"]]

    def test_a_failed_batched_read_retries_every_path(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        monkeypatch.setattr(reconciler_module, "_BULK_RETOKEN", 2)
        store, rec = self._untokened(tmp_path)
        batched = _BatchedRows(store)

        def refuse(_paths: Any) -> dict[str, list[VectorRow]]:
            raise OSError("connection lost")

        monkeypatch.setattr(batched, "rows_by_path", refuse)
        with caplog.at_level(logging.WARNING):
            result = rec.reconcile(batched, site="test")
        assert (result.retry, result.retokened) == (set(THREE), 0)
        assert set(store.tokens().values()) == {None}
        assert any(
            r.msg.startswith("embedding_reconcile_bulk_write_failed")
            for r in caplog.records
        )

    def test_a_preview_reads_own_rows_in_batches_past_a_missing_document(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(reconciler_module, "_ROWS_BATCH", 2)
        _fts, _provider, store, rec = _setup(tmp_path, THREE)
        rec.rebuild(store, store.identity)
        store.remove_document("b.md", expected_token=ANY_TOKEN)
        batched = _BatchedRows(store)
        preview = rec.preview(batched, store.identity, reuse_own=True)
        assert (preview.reusable, preview.to_embed) == (2, 1)
        assert batched.row_batches == [["a.md", "b.md"], ["c.md"]]
        assert batched.rows_read == []

    def test_a_store_without_batches_reads_each_document(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(reconciler_module, "_BULK_RETOKEN", 2)
        store, rec = self._untokened(tmp_path)
        read: list[str] = []
        real = store.rows_for
        monkeypatch.setattr(store, "rows_for", lambda p: read.append(p) or real(p))
        assert rec.reconcile(store, site="test").retokened == 3
        assert set(read) == set(THREE)


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

    def test_a_tombstone_keeps_without_asking_the_source(self, tmp_path):
        probe = _Probe()
        fts, store, rec = self._orphan(tmp_path, probe=probe)
        fts.upsert_tombstone(
            SkippedFile(path="a.md", category="parse_error", detail="x"),
            content_hash="h",
            modified_at=0.0,
        )
        rec.reconcile(store, site="test")
        # The note exists but does not parse: kept until fixed or deleted (#1505).
        assert sorted(metadata_by_path(store)) == ["a.md", "b.md"]
        assert probe.asked == []

    def test_an_excluded_tombstone_is_removed(self, tmp_path):
        probe = _Probe()
        fts, store, rec = self._orphan(tmp_path, probe=probe)
        fts.upsert_tombstone(
            SkippedFile(path="a.md", category="parse_error", detail="x"),
            content_hash="h",
            modified_at=0.0,
        )
        rec._is_excluded = lambda path: path == "a.md"  # the exclusion wins
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
        body = "## One\n\nApples.\n\n## Two\n\nPears.\n\n## Three\n\nPlums.\n"
        notes = {"a.md": f"# A\n\n{body}\n## One\n\nApples.\n\n## Four\n\nEnd.\n"}
        _fts, provider, store, rec = _setup(tmp_path, notes, chunker=SECTIONS)
        doc = rec.documents()["a.md"]
        assert len(doc.texts) >= 4
        assert len(set(doc.texts)) < len(doc.texts)  # a duplicated section
        donor = _donate(rec, ["a.md"])
        missing = doc.texts[1]
        del donor.given["a.md"][missing]
        outcome = rec.rebuild(store, store.identity, donor=donor)
        assert provider.texts == [missing]
        assert outcome.embedded == doc.texts.count(missing)
        assert outcome.reused == len(doc.texts) - doc.texts.count(missing)
        rows = store.rows_for("a.md")
        layout = [
            (r.metadata.get("heading"), r.metadata.get("start_line")) for r in rows
        ]
        assert layout == [(m.get("heading"), m.get("start_line")) for m in doc.metadata]
        for row, text in zip(rows, doc.texts, strict=True):
            if text != missing:
                assert np.allclose(row.vector, donor.given["a.md"][text])

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
        donor = _FakeDonor({}, fail=True)
        with pytest.raises(StoreUnavailableError):
            rec.rebuild(store, store.identity, donor=donor)
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
        assert lines == [("fake:donor", outcome.reused, outcome.embedded, 2, True)]

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


def test_rebuild_releases_documents_before_embedding(tmp_path, monkeypatch):
    """A reused tail must not keep all prepared documents alive at provider time."""
    import weakref

    notes = {f"{i:03}.md": f"# Note {i}\n\nContent {i}.\n" for i in range(12)}
    fts, provider, store, rec = _setup(tmp_path, notes)
    rec.rebuild(store, store.identity)
    (tmp_path / "000.md").write_text("# Changed\n\nNew content.\n")
    fts.upsert_note(parse_note(tmp_path / "000.md", tmp_path, HeadingChunker()))
    references = []
    document = reconciler_module._Document

    def track(*args, **kwargs):
        result = document(*args, **kwargs)
        references.append(weakref.ref(result))
        return result

    embed = provider.embed

    def observe(texts):
        assert sum(ref() is not None for ref in references) <= 2
        return embed(texts)

    monkeypatch.setattr(reconciler_module, "_ROWS_BATCH", 2)
    monkeypatch.setattr(reconciler_module, "_Document", track)
    monkeypatch.setattr(provider, "embed", observe)
    outcome = rec.rebuild(store, store.identity, reuse_own=True)
    assert outcome.published


def test_rebuild_replays_generation_and_cleans_scratch(tmp_path, monkeypatch):
    import tempfile

    original = tempfile.TemporaryFile
    handles = []

    def track(*args, **kwargs):
        handle = original(*args, **kwargs)
        handles.append(handle)
        return handle

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(tempfile, "TemporaryFile", track)
    _, _provider, store, rec = _setup(tmp_path, NOTES)
    publish = store.publish_generation
    observed = []

    def replay(identity, generation):
        first = list(generation)
        second = [(row, list(generation)) for row in generation]
        assert [row for row, _ in second] == first
        assert all(nested == first for _, nested in second)
        assert all(
            isinstance(row.vector, array) and row.vector.typecode == "f"
            for doc in first
            for row in doc.rows
        )
        observed.extend(first)
        return publish(identity, generation)

    monkeypatch.setattr(store, "publish_generation", replay)
    assert rec.rebuild(store, store.identity).published
    assert len(observed) == 2
    assert len(handles) == 2 and all(handle.closed for handle in handles)


def test_iter_chunks_matches_list_across_document_pages(tmp_path, monkeypatch):
    import markdown_vault_mcp.fts_index as fts_module

    notes = {
        "a.md": "# A\n\nOne.\n\n## First\n\nTwo.\n\n## Second\n\nThree.\n",
        "b.md": "# B\n\nFour.\n",
        "c.md": "# C\n\nFive.\n\n## Third\n\nSix.\n",
    }
    fts = _index(tmp_path, notes, HeadingChunker(short_doc_lines=0))
    monkeypatch.setattr(fts_module, "_CHUNK_PAGE_DOCS", 1)
    rows = list(fts.iter_chunks())
    assert rows == fts.list_chunks()
    assert [row["path"] for row in rows] == [
        "a.md",
        "a.md",
        "a.md",
        "b.md",
        "c.md",
        "c.md",
    ]
    assert [row["is_first_chunk"] for row in rows] == [
        True,
        False,
        False,
        True,
        True,
        False,
    ]
    assert [row["heading"] for row in rows] == [
        "A",
        "First",
        "Second",
        "B",
        "C",
        "Third",
    ]


def test_custom_index_fallback_groups_interleaved_chunks(tmp_path, monkeypatch):
    fts, provider, store, rec = _setup(tmp_path, NOTES)
    rows = fts.list_chunks()
    # The required protocol does not promise that a document's rows are adjacent.
    rows = [
        rows[0],
        rows[1],
        {**rows[0], "content": "Second chunk", "is_first_chunk": False},
    ]

    class CustomIndex:
        def list_chunks(self, paths=None):
            return (
                rows if paths is None else [row for row in rows if row["path"] in paths]
            )

    calls = []
    embed = provider.embed

    def capture(texts):
        calls.extend(texts)
        return embed(texts)

    monkeypatch.setattr(rec, "_fts", CustomIndex())
    monkeypatch.setattr(provider, "embed", capture)
    result = rec.rebuild(store, store.identity)
    assert result.published
    assert calls == ["# A\n\nApples.", "Second chunk", "# B\n\nBoats."]
    assert len(store.rows_for("a.md")) == 2


def test_stage_closes_retained_replay_on_publisher_failure(
    tmp_path, monkeypatch, caplog
):
    import tempfile

    from tests.fixtures.embedding_stage import install_cleanup_failure

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    _, _, store, rec = _setup(tmp_path, NOTES)
    retained = []

    def fail(_identity, generation):
        reader = iter(generation)
        next(reader)
        retained.append(reader)
        raise OSError("publish failed")

    monkeypatch.setattr(store, "publish_generation", fail)
    install_cleanup_failure(monkeypatch)
    with pytest.raises(OSError, match="publish failed"):
        rec.rebuild(store, store.identity)
    assert not list(tmp_path.glob("embedding-stage-*"))
    assert "embedding_stage_cleanup_failed" in caplog.text
    with pytest.raises(ValueError, match="closed"):
        next(retained[0])


def test_stage_cleans_up_when_open_fails(tmp_path, monkeypatch):
    import tempfile

    from markdown_vault_mcp.indexing._embedding_stage import EmbeddingStage

    original = tempfile.TemporaryFile
    opened = []
    failure = OSError("scratch full")

    def fail_second(*args, **kwargs):
        if opened:
            raise failure
        handle = original(*args, **kwargs)
        opened.append(handle)
        return handle

    monkeypatch.setattr(tempfile, "TemporaryFile", fail_second)
    with pytest.raises(OSError) as caught:
        EmbeddingStage(tmp_path)
    assert caught.value is failure
    assert len(opened) == 1 and opened[0].closed
    assert not list(tmp_path.iterdir())


def test_late_chunk_page_seeks_past_earlier_documents():
    index = FTSIndex(db_path=":memory:")
    conn = index._conn()
    conn.executemany(
        "INSERT INTO documents(path,title,content_hash,modified_at) VALUES(?,?,?,?)",
        ((f"{i:05}.md", "T", "h", 0) for i in range(8192)),
    )
    conn.executemany(
        "INSERT INTO sections(document_id,heading_level,content,start_line) VALUES(?,?,?,?)",
        ((i, 1, "body", 1) for i in range(1, 8193)),
    )
    conn.commit()

    def steps(after):
        calls = 0

        def progress():
            nonlocal calls
            calls += 1
            return 0

        conn.set_progress_handler(progress, 100)
        rows = index._chunk_page(after)
        conn.set_progress_handler(None, 0)
        return calls, rows

    try:
        first, _ = steps(None)
        last, rows = steps("07935.md")
        assert rows[0]["path"] == "07936.md"
        assert last <= 2 * first, (first, last)
    finally:
        index.close()


@pytest.mark.parametrize("primary", [True, False])
def test_stage_attempts_both_closes_after_failure(
    tmp_path, monkeypatch, caplog, primary
):
    from markdown_vault_mcp.indexing._embedding_stage import EmbeddingStage

    stage = EmbeddingStage(tmp_path)
    attempts = []
    for name in ("_docs", "_results"):
        spool = getattr(stage, name)
        close = spool.close

        def fail(close=close, name=name):
            attempts.append(name)
            close()
            raise OSError(f"{name} close failed")

        monkeypatch.setattr(spool, "close", fail)
    if primary:
        with pytest.raises(RuntimeError, match="donor failed"), stage:
            raise RuntimeError("donor failed")
    else:
        with stage:
            pass
    assert attempts == ["_docs", "_results"]
    assert caplog.text.count("embedding_stage_cleanup_failed") == 2
    assert not list(tmp_path.iterdir())


def test_rebuild_uses_real_fts_chunk_iterator(tmp_path, monkeypatch):
    import markdown_vault_mcp.fts_index as fts_module

    notes = {f"{i:03}.md": f"# Note {i}\n\nContent {i}.\n" for i in range(5)}
    fts, _, store, rec = _setup(tmp_path, notes)
    monkeypatch.setattr(fts_module, "_CHUNK_PAGE_DOCS", 2)
    original = fts.iter_chunks
    calls = []

    def iterate():
        calls.append(True)
        return original()

    def unexpected(*_args, **_kwargs):
        pytest.fail("rebuild used eager list_chunks")

    monkeypatch.setattr(fts, "iter_chunks", iterate)
    monkeypatch.setattr(fts, "list_chunks", unexpected)
    outcome = rec.rebuild(store, store.identity)
    assert outcome.published and outcome.attempted == 5
    assert calls == [True]
    assert set(store.tokens()) == set(notes)


def test_custom_metadata_retains_objects_during_replay(tmp_path, monkeypatch):
    fts, _, store, rec = _setup(tmp_path, {"a.md": "# A\n\nApple\n"})
    line = np.int64(3)
    rows = [{**row, "start_line": line} for row in fts.list_chunks()]

    class Index:
        def list_chunks(self, paths=None):
            return (
                rows if paths is None else [row for row in rows if row["path"] in paths]
            )

    class StreamingIndex(Index):
        def iter_chunks(self):
            return iter(self.list_chunks())

    seen = []

    def publish(_identity, generation):
        seen.extend([list(generation), list(generation)])

    monkeypatch.setattr(rec, "_fts", StreamingIndex())
    monkeypatch.setattr(store, "publish_generation", publish)
    assert rec.rebuild(store, store.identity).published
    for generation in seen:
        metadata = generation[0].rows[0].metadata
        assert metadata["start_line"] is line


def test_scratch_dir_bypasses_unavailable_default_temp(tmp_path, monkeypatch):
    import tempfile

    _, _, store, rec = _setup(tmp_path, NOTES)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "missing"))
    rec._scratch_dir = tmp_path / "state"
    assert rec.rebuild(store, store.identity).published


def test_unavailable_scratch_fails_before_provider_and_keeps_store(tmp_path):
    _, provider, store, rec = _setup(tmp_path, NOTES)
    rec.rebuild(store, store.identity)
    before = store.tokens()
    provider.texts.clear()
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("fixture")
    rec._scratch_dir = blocked / "stage"
    with pytest.raises(OSError):
        rec.rebuild(store, store.identity)
    assert provider.texts == []
    assert store.tokens() == before


def test_source_failure_preserves_generation(tmp_path, monkeypatch):
    fts, _, store, rec = _setup(tmp_path, NOTES)
    rec.rebuild(store, store.identity)
    before = store.tokens()

    def fail(*_args, **_kwargs):
        raise RuntimeError("source failed")

    monkeypatch.setattr(fts, "iter_chunks", fail)
    with pytest.raises(RuntimeError, match="source failed"):
        rec.rebuild(store, store.identity)
    assert store.tokens() == before


def test_chunk_iterator_observes_changes_on_later_pages(tmp_path, monkeypatch):
    import markdown_vault_mcp.fts_index as fts_module

    fts = _index(tmp_path, {f"{i}.md": f"# Note {i}\n\nBody {i}.\n" for i in range(5)})
    monkeypatch.setattr(fts_module, "_CHUNK_PAGE_DOCS", 2)
    reader = fts.iter_chunks()
    first = [next(reader), next(reader)]
    fts.delete_by_path("2.md")
    fts.delete_by_path("3.md")
    (tmp_path / "4.md").write_text("# Changed\n\nNew body.\n")
    (tmp_path / "5.md").write_text("# New\n\nNew entry.\n")
    for name in ("4.md", "5.md"):
        fts.upsert_note(parse_note(tmp_path / name, tmp_path, HeadingChunker()))
    rows = first + list(reader)
    assert [row["path"] for row in rows] == ["0.md", "1.md", "4.md", "5.md"]
    assert "New body" in rows[2]["content"]


def test_chunk_page_selects_paths_and_rows_in_one_statement(tmp_path):
    fts = _index(tmp_path, {"a.md": "# A\n\nApple.\n"})
    queries = []
    conn = fts._conn()
    conn.set_trace_callback(queries.append)
    try:
        assert fts._chunk_page(None)
    finally:
        conn.set_trace_callback(None)
    assert (
        len(
            [
                query
                for query in queries
                if query.lstrip().upper().startswith(("SELECT", "WITH"))
            ]
        )
        == 1
    )


def test_provider_error_is_not_logged_as_scratch_failure(tmp_path, caplog):
    from markdown_vault_mcp.indexing._embedding_stage import EmbeddingStage

    failure = OSError("provider unavailable")

    def results():
        raise failure
        yield  # pragma: no cover

    with EmbeddingStage(tmp_path) as stage:
        stage.finish()
        with pytest.raises(OSError) as caught:
            stage.write_results(results())
    assert caught.value is failure
    assert "embedding_stage_io_failed" not in caplog.text


@pytest.mark.parametrize("operation", ["rebuild", "preview"])
def test_memory_writer_during_donor_read(tmp_path, monkeypatch, operation):
    from threading import Thread

    import markdown_vault_mcp.fts_index as fts_module
    import markdown_vault_mcp.indexing.embedding_reconciler as reconciler_module
    from markdown_vault_mcp.scanner import HeadingChunker, parse_note

    fts, _, store, rec = _setup(
        tmp_path, {f"{i}.md": f"# {i}\n\nBody {i}.\n" for i in range(3)}
    )
    monkeypatch.setattr(fts_module, "_CHUNK_PAGE_DOCS", 1)
    monkeypatch.setattr(reconciler_module, "_ROWS_BATCH", 1)
    errors = []
    updated = []

    def write():
        try:
            (tmp_path / "2.md").write_text("# Updated\n\nNew body.\n")
            fts.upsert_note(parse_note(tmp_path / "2.md", tmp_path, HeadingChunker()))
            updated.append(True)
        except Exception as error:
            errors.append(error)

    class Donor:
        source = "fixture"
        refused_rows = 0

        def vectors_for(self, _path):
            if not updated and not errors:
                thread = Thread(target=write)
                thread.start()
                thread.join()
            return {}

    try:
        result = getattr(rec, operation)(store, store.identity, donor=Donor())
        assert not errors
        assert updated
        if operation == "rebuild":
            assert result.published and set(store.tokens()) == {"0.md", "1.md", "2.md"}
            assert "New body" in store.rows_for("2.md")[0].metadata["content"]
        else:
            assert result.chunks == 3
    finally:
        fts.close()
        store.close()


def test_custom_chunk_iterator_closed_when_stage_fails(tmp_path, monkeypatch):
    from markdown_vault_mcp.indexing._embedding_stage import EmbeddingStage

    fts, _, store, rec = _setup(
        tmp_path, {"a.md": "# A\n\nApple\n", "b.md": "# B\n\nBerry\n"}
    )
    closed = []
    rows = fts.list_chunks()

    def source():
        try:
            yield from rows
        finally:
            closed.append(True)

    retained = source()

    class CustomIndex:
        def list_chunks(self, paths=None):
            return (
                rows if paths is None else [row for row in rows if row["path"] in paths]
            )

        def iter_chunks(self):
            return retained

    def fail(*_args, **_kwargs):
        assert not closed
        raise RuntimeError("stage failed")

    monkeypatch.setattr(reconciler_module, "_ROWS_BATCH", 1)
    monkeypatch.setattr(rec, "_fts", CustomIndex())
    monkeypatch.setattr(EmbeddingStage, "add", fail)
    with pytest.raises(RuntimeError, match="stage failed"):
        rec.rebuild(store, store.identity)
    assert closed == [True]
    fts.close()
    store.close()


@pytest.mark.parametrize("point", ["allocation", "discovery", "write", "read"])
def test_scratch_failure_logs_location_and_preserves_exception(
    tmp_path, monkeypatch, caplog, point
):
    import json
    import tempfile

    from markdown_vault_mcp.indexing._embedding_stage import EmbeddingStage
    from markdown_vault_mcp.indexing.embedding_reconciler import _Document

    failure = OSError("synthetic scratch failure")

    def fail(*_args, **_kwargs):
        raise failure

    with caplog.at_level(logging.ERROR), pytest.raises(OSError) as caught:
        if point == "discovery":
            monkeypatch.setattr(tempfile, "gettempdir", fail)
            EmbeddingStage()
        elif point == "allocation":
            monkeypatch.setattr(tempfile, "TemporaryFile", fail)
            EmbeddingStage(tmp_path)
        else:
            with EmbeddingStage(tmp_path) as stage:
                if point == "write":
                    monkeypatch.setattr(json, "dumps", fail)
                stage.add(
                    _Document(
                        "a.md",
                        "a",
                        ("text",),
                        (
                            {
                                "path": "a.md",
                                "title": "A",
                                "folder": "",
                                "heading": None,
                                "content": "text",
                                "start_line": 1,
                                "preamble": "",
                            },
                        ),
                    ),
                    {},
                )
                if point == "read":
                    stage.finish()
                    monkeypatch.setattr(stage._docs, "read", fail)
                    list(stage.misses())
    assert caught.value is failure
    assert "embedding_stage_io_failed" in caplog.text
    assert (
        "system temporary directories" if point == "discovery" else str(tmp_path)
    ) in caplog.text
    assert "synthetic scratch failure" in caplog.text


def test_mixed_reuse_and_duplicate_misses_across_windows(tmp_path, monkeypatch):
    from markdown_vault_mcp.indexing.embedding_reconciler import _Document

    _, provider, store, rec = _setup(tmp_path, NOTES)

    def meta(path, content):
        return {
            "path": path,
            "title": "Title",
            "folder": "",
            "heading": None,
            "content": content,
            "start_line": 1,
            "preamble": "",
        }

    own = array("f", [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    donated = array("f", [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    store.publish_generation(
        store.identity,
        [DocumentRows("a.md", "old", (VectorRow(own, meta("a.md", "own")),))],
    )
    docs = [
        _Document(
            "a.md",
            "a",
            ("own", "donor", "miss", "miss"),
            tuple(meta("a.md", x) for x in ("own", "donor", "miss", "miss")),
        ),
        _Document(
            "b.md",
            "b",
            ("miss", "later"),
            tuple(meta("b.md", x) for x in ("miss", "later")),
        ),
        _Document("c.md", "c", ("last",), (meta("c.md", "last"),)),
    ]
    calls = []
    embed = provider.embed

    class Donor:
        source = "fixture"
        refused_rows = 0

        def vectors_for(self, _path):
            return {"own": donated, "donor": donated}

    def capture(texts):
        calls.append(list(texts))
        return embed(texts)

    monkeypatch.setattr(rec, "_build_documents", lambda: (doc for doc in docs))
    monkeypatch.setattr(reconciler_module, "_ROWS_BATCH", 1)
    monkeypatch.setattr(rec, "_batch_size", 3)
    monkeypatch.setattr(provider, "embed", capture)
    preview = rec.preview(store, store.identity, donor=Donor(), reuse_own=True)
    assert (preview.chunks, preview.reusable, preview.to_embed) == (7, 2, 4)
    result = rec.rebuild(store, store.identity, donor=Donor(), reuse_own=True)
    assert (result.reused, result.embedded) == (2, 5)
    assert calls == [["miss", "miss", "later"], ["last"]]
    rows = store.rows_for("a.md")
    assert list(rows[0].vector) == list(own)
    assert list(rows[1].vector) == list(donated)
    assert list(rows[2].vector) == list(rows[3].vector)


def test_stage_counts_and_fans_out_duplicate_misses(tmp_path):
    from markdown_vault_mcp.indexing._embedding_stage import EmbeddingStage
    from markdown_vault_mcp.indexing.embedding_reconciler import _Document

    doc = _Document(
        "a.md",
        "a",
        ("reuse", "miss", "miss"),
        tuple(
            {
                "path": "a.md",
                "title": "A",
                "folder": "",
                "heading": None,
                "content": text,
                "start_line": 1,
                "preamble": "",
            }
            for text in ("reuse", "miss", "miss")
        ),
    )
    with EmbeddingStage(tmp_path) as stage:
        stage.add(doc, {0: array("f", [1.0])})
        stage.finish()
        assert stage.miss_count == 1
        assert list(stage.misses()) == [("miss", 2)]


@pytest.mark.parametrize("fallback", [False, True])
def test_written_stage_files_do_not_survive_process_death(tmp_path, fallback):
    import os
    import subprocess
    import sys

    state = tmp_path / "state"
    state.mkdir()
    program = r"""
import os
import sys
import tempfile
from array import array
from pathlib import Path
from markdown_vault_mcp.indexing._embedding_stage import EmbeddingStage
from markdown_vault_mcp.indexing.embedding_reconciler import _Document
if sys.argv[2] == "fallback":
    tempfile._O_TMPFILE_WORKS = False
root = Path(sys.argv[1])
meta = {"path": "a.md", "title": "A", "folder": "", "heading": None,
        "content": "synthetic corpus content", "start_line": 1, "preamble": ""}
stage = EmbeddingStage(root)
stage.add(_Document("a.md", "token", ("synthetic corpus content",), (meta,)), {})
stage.finish()
stage.write_results((vector for vector in [array("f", [1.0, 2.0])]))
assert stage.miss_count == 1
os._exit(137)
"""
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            str(state),
            "fallback" if fallback else "default",
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert child.returncode == 137, child.stderr
    assert list(state.iterdir()) == [], "written scratch survived abrupt process death"
