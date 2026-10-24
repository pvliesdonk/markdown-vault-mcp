"""An unparseable note keeps its vectors until it is fixed or deleted (#1505)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.scanner import HeadingChunker
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider

if TYPE_CHECKING:
    from pathlib import Path

_ORCHARD = "Apples and pears grow in the orchard beside the old stone wall. " * 12
_HARVEST = "The harvest is in autumn, when the whole village helps to pick. " * 12
ALPHA = f"---\ntitle: Alpha\n---\n# Alpha\n\n{_ORCHARD}\n\n## Harvest\n\n{_HARVEST}\n"
BETA = "---\ntitle: Beta\n---\n# Beta\n\nBoats sail across the harbour.\n"
BROKEN = {
    "parse_error": ALPHA.replace("title: Alpha", "title: [unclosed"),
    "missing_frontmatter": ALPHA.replace("title: Alpha", "kind: note"),
}


class _Counting(MockEmbeddingProvider):
    """``mock/mock-dim-32``, counting embedded texts."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return super().embed(texts)


def _vault(tmp_path: Path, provider: Any, **settings: Any) -> Vault:
    return Vault(
        source_dir=tmp_path / "vault",
        chunk_strategy=HeadingChunker(short_doc_lines=0),  # alpha.md: two chunks
        settings=VaultSettings(
            index_path=tmp_path / "index.db",
            read_only=False,
            required_frontmatter=["title"],
            **settings,
        ),
        embedding_provider=provider,
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "alpha.md").write_text(ALPHA)
    (vault / "beta.md").write_text(BETA)
    return vault


def _built(tmp_path: Path, provider: Any, **settings: Any) -> Vault:
    vault = _vault(tmp_path, provider, **settings)
    vault.index.build_index()
    vault.index.build_embeddings()
    return vault


def _tokens(vault: Vault) -> dict[str, Any]:
    store = vault._vectors
    assert store is not None
    return dict(store.tokens())


def _semantic_paths(vault: Vault, query: str) -> set[str]:
    return {r.path for r in vault.reader.search(query, mode="semantic", limit=10)}


def _settle(vault: Vault) -> None:
    vault.index.reindex()
    vault.index.build_embeddings()


@pytest.mark.parametrize("category", sorted(BROKEN))
def test_a_broken_note_keeps_its_vectors(
    tmp_path: Path, root: Path, category: str
) -> None:
    provider = _Counting()
    vault = _built(tmp_path, provider)
    try:
        provider.texts.clear()
        (root / "alpha.md").write_text(BROKEN[category])
        _settle(vault)
        skipped = vault.index.get_index_status()["skipped_files"]
        assert [(s["path"], s["category"]) for s in skipped] == [("alpha.md", category)]
        assert "alpha.md" in _tokens(vault)  # kept
        assert provider.texts == []  # nothing embedded
        assert "alpha.md" not in _semantic_paths(vault, "Apples and pears")
    finally:
        vault.close()


def test_a_fixed_note_reuses_its_unchanged_chunks(tmp_path: Path, root: Path) -> None:
    provider = _Counting()
    vault = _built(tmp_path, provider)
    try:
        before = _tokens(vault)["alpha.md"]
        (root / "alpha.md").write_text(BROKEN["parse_error"])
        _settle(vault)
        provider.texts.clear()
        (root / "alpha.md").write_text(
            ALPHA.replace(_HARVEST, _HARVEST.replace("autumn", "October"))
        )
        _settle(vault)
        assert len(provider.texts) == 1  # only the changed section, before any search
        assert _tokens(vault)["alpha.md"] != before  # the fixed note's token
        assert "alpha.md" in _semantic_paths(vault, "Apples and pears")
    finally:
        vault.close()


def test_a_broken_note_deleted_offline_loses_its_vectors(
    tmp_path: Path, root: Path
) -> None:
    vault = _built(tmp_path, _Counting())
    try:
        (root / "alpha.md").write_text(BROKEN["parse_error"])
        _settle(vault)
        assert "alpha.md" in _tokens(vault)
    finally:
        vault.close()
    (root / "alpha.md").unlink()
    again = _vault(tmp_path, _Counting())
    try:
        again.index.build_index()
        _settle(again)
        assert "alpha.md" not in _tokens(again)
    finally:
        again.close()


def test_an_excluded_broken_note_loses_its_vectors(tmp_path: Path, root: Path) -> None:
    vault = _built(tmp_path, _Counting())
    try:
        (root / "alpha.md").write_text(BROKEN["parse_error"])
        _settle(vault)
    finally:
        vault.close()
    again = _vault(tmp_path, _Counting(), exclude_patterns=["alpha.md"])
    try:
        again.index.build_index()
        _settle(again)
        assert "alpha.md" not in _tokens(again)
    finally:
        again.close()


def test_a_tombstone_is_kept_quietly(
    tmp_path: Path, root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    vault = _built(tmp_path, _Counting())
    try:
        (root / "alpha.md").write_text(BROKEN["parse_error"])
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger="markdown_vault_mcp"):
            _settle(vault)
            _settle(vault)
    finally:
        vault.close()
    kept = [r for r in caplog.records if r.msg.startswith("embedding_reconcile_kept")]
    assert kept
    assert {r.levelname for r in kept} == {"DEBUG"}
    assert all(r.msg.endswith("reason=tombstoned") for r in kept)


def test_a_full_rebuild_drops_a_broken_note(tmp_path: Path, root: Path) -> None:
    vault = _built(tmp_path, _Counting())
    try:
        (root / "alpha.md").write_text(BROKEN["parse_error"])
        _settle(vault)
        vault.index.build_embeddings(force=True)
        assert set(_tokens(vault)) == {"beta.md"}
    finally:
        vault.close()


def test_a_broken_note_deleted_while_running_loses_its_vectors(
    tmp_path: Path, root: Path
) -> None:
    vault = _built(tmp_path, _Counting())
    try:
        (root / "alpha.md").write_text(BROKEN["parse_error"])
        _settle(vault)
        (root / "alpha.md").unlink()
        vault.index.reindex()  # what the watcher runs; no full converge
        assert "alpha.md" not in _tokens(vault)
    finally:
        vault.close()


def test_a_broken_note_renamed_while_running_loses_its_old_vectors(
    tmp_path: Path, root: Path
) -> None:
    vault = _built(tmp_path, _Counting())
    try:
        (root / "alpha.md").write_text(BROKEN["parse_error"])
        _settle(vault)
        (root / "alpha.md").rename(root / "renamed.md")
        vault.index.reindex()
        assert "alpha.md" not in _tokens(vault)
    finally:
        vault.close()


def test_a_note_that_is_not_utf8_keeps_its_vectors(tmp_path: Path, root: Path) -> None:
    vault = _built(tmp_path, _Counting())
    try:
        (root / "alpha.md").write_bytes(ALPHA.encode() + b"\xff\xfe broken")
        _settle(vault)
        skipped = vault.index.get_index_status()["skipped_files"]
        assert [s["category"] for s in skipped] == ["encoding_error"]
        assert "alpha.md" in _tokens(vault)
    finally:
        vault.close()
