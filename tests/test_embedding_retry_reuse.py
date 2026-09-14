"""Retry FTS/graph work without paying again for completed embeddings (#1464)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.indexing import FlushDirtyEmbeddings
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import MockEmbeddingProvider

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_CONTENT = (
    "---\ntitle: Note\nsummary: First\n---\n# Section\n\nBody\n\n## Tail\n\nTail body\n"
)


class _CountingProvider(MockEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.fail_next = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("provider unavailable")
        return super().embed(texts)


@pytest.fixture
def seeded(tmp_path: Path) -> Iterator[tuple[Vault, _CountingProvider]]:
    provider = _CountingProvider()
    (tmp_path / "note.md").write_text(_CONTENT)
    col = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(
            read_only=False,
            embeddings_path=tmp_path / ".vectors",
            embed_context=True,
            searchable_frontmatter_fields=["summary"],
        ),
        embedding_provider=provider,
    )
    try:
        col.index.build_index()
        col.index.build_embeddings()
        provider.calls = 0
        yield col, provider
    finally:
        col.close()


def _flush(col: Vault) -> None:
    col._coordinator.writer.mark_embedding_dirty(["note.md"])
    col._coordinator.writer.submit(FlushDirtyEmbeddings()).result(5)


@pytest.mark.parametrize(
    "content",
    [
        _CONTENT.replace("Body", "Changed body"),
        _CONTENT.replace("title: Note", "title: Renamed"),
        _CONTENT.replace("# Section", "# New heading"),
        _CONTENT.replace("\n## Tail", "\n\n## Tail"),
        _CONTENT.replace("summary: First", "summary: Second"),
        _CONTENT + "\n## Second section\n\nMore content\n",
    ],
    ids=["body", "title", "heading", "position", "preamble", "chunks"],
)
def test_changed_input_refreshes_once(
    seeded: tuple[Vault, _CountingProvider], content: str
) -> None:
    col, provider = seeded
    (col.source_dir / "note.md").write_text(content)
    _flush(col)
    assert provider.calls == 1
    _flush(col)
    assert provider.calls == 1


def test_failed_provider_attempt_is_retried(
    seeded: tuple[Vault, _CountingProvider],
) -> None:
    col, provider = seeded
    (col.source_dir / "note.md").write_text(_CONTENT.replace("Body", "Changed"))
    provider.fail_next = True
    with pytest.raises(RuntimeError, match="provider unavailable"):
        _flush(col)
    assert col.index.get_index_status()["dirty_embeddings"] == 1
    _flush(col)
    assert provider.calls == 2
    _flush(col)
    assert provider.calls == 2


def test_failed_save_retries_without_reembedding(
    seeded: tuple[Vault, _CountingProvider], monkeypatch: pytest.MonkeyPatch
) -> None:
    col, provider = seeded
    (col.source_dir / "note.md").write_text(_CONTENT.replace("Body", "Changed"))
    assert col._vectors is not None
    original = col._vectors.save
    saves = 0

    def fail_once(path: Path) -> None:
        nonlocal saves
        saves += 1
        if saves == 1:
            raise OSError("save failed")
        original(path)

    monkeypatch.setattr(col._vectors, "save", fail_once)
    with pytest.raises(OSError, match="save failed"):
        _flush(col)
    _flush(col)
    assert saves == 2
    assert provider.calls == 1


def test_deleted_and_recreated_note_is_embedded_again(
    seeded: tuple[Vault, _CountingProvider],
) -> None:
    col, provider = seeded
    note = col.source_dir / "note.md"
    note.unlink()
    _flush(col)
    assert col._vectors is not None
    assert "note.md" not in col._vectors.chunks_by_path()
    note.write_text(_CONTENT)
    _flush(col)
    assert provider.calls == 1
    assert "note.md" in col._vectors.chunks_by_path()


def test_persisted_vectors_are_reused_after_reload(
    seeded: tuple[Vault, _CountingProvider],
) -> None:
    col, provider = seeded
    col._vectors = None
    _flush(col)
    assert provider.calls == 0
    assert col._vectors is not None
    assert "note.md" in col._vectors.chunks_by_path()
