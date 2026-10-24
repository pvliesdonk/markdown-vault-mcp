"""An empty vault folder removes nothing until notes reappear (#1791, #1505)."""

from __future__ import annotations

import logging
import shutil
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider

if TYPE_CHECKING:
    from pathlib import Path

NOTES = {
    "a.md": "# Alpha\n\nApples and pears.\n",
    "b.md": "# Beta\n\nBoats in the harbour.\n",
}


class _Counting(MockEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return super().embed(texts)


def _vault(tmp_path: Path, provider: Any = None, **settings: Any) -> Vault:
    return Vault(
        source_dir=tmp_path / "vault",
        settings=VaultSettings(
            index_path=tmp_path / "index.db", read_only=False, **settings
        ),
        embedding_provider=provider or _Counting(),
    )


@pytest.fixture
def built(tmp_path: Path) -> Path:
    """Two notes indexed and embedded; the vault is closed."""
    root = tmp_path / "vault"
    root.mkdir()
    for name, text in NOTES.items():
        (root / name).write_text(text)
    vault = _vault(tmp_path)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
    finally:
        vault.close()
    return root


def _boot(vault: Vault) -> Any:
    vault.index.build_index()
    result = vault.index.reindex()
    vault.index.build_embeddings()
    return result


def _state(vault: Vault) -> tuple[set[str], set[str]]:
    store = vault._vectors
    tokens = set(store.tokens()) if store is not None else set()
    return {r["path"] for r in vault._fts.list_notes()}, tokens


def _refusals(caplog: pytest.LogCaptureFixture) -> list[Any]:
    return [
        r.args
        for r in caplog.records
        if r.msg.startswith("vault_root_empty_removals_refused")
    ]


def test_an_empty_mount_point_removes_nothing_and_recovers(
    tmp_path: Path, built: Path, caplog: pytest.LogCaptureFixture
) -> None:
    aside = tmp_path / "aside"
    shutil.move(built, aside)
    built.mkdir()  # the mount point stays, empty
    vault = _vault(tmp_path)
    try:
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            _boot(vault)
        assert _state(vault) == (set(NOTES), set(NOTES))
        assert _refusals(caplog)
        assert all(args == (2,) for args in _refusals(caplog))
    finally:
        vault.close()
    built.rmdir()
    shutil.move(aside, built)  # the volume is back
    provider = _Counting()
    again = _vault(tmp_path, provider)
    try:
        result = _boot(again)
        assert (result.added, result.deleted) == (0, 0)
        assert _state(again) == (set(NOTES), set(NOTES))
        assert provider.texts == []
    finally:
        again.close()


def test_a_deleted_note_is_still_removed_when_others_remain(
    tmp_path: Path, built: Path
) -> None:
    (built / "a.md").unlink()
    vault = _vault(tmp_path)
    try:
        result = _boot(vault)
        assert result.deleted == 1
        assert _state(vault) == ({"b.md"}, {"b.md"})
    finally:
        vault.close()


def test_a_forced_rebuild_empties_an_empty_vault(tmp_path: Path, built: Path) -> None:
    for name in NOTES:
        (built / name).unlink()
    vault = _vault(tmp_path)
    try:
        vault.index.build_index(force=True)
        vault.index.reindex()
        vault.index.build_embeddings(force=True)
        assert _state(vault) == (set(), set())
    finally:
        vault.close()


def test_a_folder_of_only_excluded_files_refuses_keyword_removals(
    tmp_path: Path, built: Path, caplog: pytest.LogCaptureFixture
) -> None:
    for name in NOTES:
        (built / name).unlink()
    (built / ".obsidian").mkdir()
    (built / ".obsidian" / "app.json").write_text("{}")
    vault = _vault(tmp_path, exclude_patterns=[".obsidian/**"])
    try:
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            _boot(vault)
        assert _refusals(caplog)
        assert _state(vault)[0] == set(NOTES)
    finally:
        vault.close()


def test_an_empty_vault_and_an_empty_index_is_quiet(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "vault").mkdir()
    vault = _vault(tmp_path)
    try:
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            vault.index.build_index()
            vault.index.reindex()
        assert _refusals(caplog) == []
    finally:
        vault.close()


def test_a_settings_change_over_an_empty_folder_keeps_the_tracker(
    tmp_path: Path, built: Path, caplog: pytest.LogCaptureFixture
) -> None:
    aside = tmp_path / "aside"
    shutil.move(built, aside)
    built.mkdir()
    # Another indexed field: the index is rebuilt, not warm-started.
    vault = _vault(tmp_path, indexed_frontmatter_fields=["tags"])
    try:
        caplog.clear()
        with caplog.at_level(logging.INFO):
            _boot(vault)
        assert any(r.msg.startswith("index_provenance_changed") for r in caplog.records)
        assert _refusals(caplog)
        assert _state(vault) == (set(NOTES), set(NOTES))
    finally:
        vault.close()
    built.rmdir()
    shutil.move(aside, built)
    again = _vault(tmp_path, indexed_frontmatter_fields=["tags"])
    try:
        again.index.build_index()
        result = again.index.reindex()
        # The refused build left the tracker's state alone: nothing is new.
        assert (result.added, result.deleted) == (0, 0)
    finally:
        again.close()


def test_a_lost_index_beside_an_empty_folder_keeps_the_vectors(
    tmp_path: Path, built: Path, caplog: pytest.LogCaptureFixture
) -> None:
    for name in NOTES:
        (built / name).unlink()
    for lost in tmp_path.glob("index.db*"):
        lost.unlink()
    vault = _vault(tmp_path)
    try:
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            vault.index.build_index()
            vault.index.build_embeddings()
        assert _state(vault)[1] == set(NOTES)
        assert any(
            r.msg.startswith("embedding_reconcile_source_unavailable")
            for r in caplog.records
        )
    finally:
        vault.close()
