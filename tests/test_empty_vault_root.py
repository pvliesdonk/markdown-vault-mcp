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


def test_a_folder_of_only_non_note_files_is_a_vault_whose_notes_are_gone(
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
        # Not an empty mount point: the folder holds something (#1791).
        assert _refusals(caplog) == []
        assert _state(vault) == (set(), set())
    finally:
        vault.close()


@pytest.mark.usefixtures("built")
def test_an_exclusion_covering_every_note_still_purges_them(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    vault = _vault(tmp_path, exclude_patterns=["*.md"])
    try:
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            _boot(vault)
        assert _refusals(caplog) == []
        assert _state(vault) == (set(), set())
    finally:
        vault.close()


def test_index_force_is_the_deliberate_way_to_empty_a_vault(
    tmp_path: Path, built: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    import markdown_vault_mcp.providers as providers_mod
    from markdown_vault_mcp.cli import _ENV_PREFIX, app

    monkeypatch.setattr(providers_mod, "get_embedding_provider", lambda _c: _Counting())
    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", str(built))
    monkeypatch.setenv(f"{_ENV_PREFIX}_INDEX_PATH", str(tmp_path / "index.db"))
    monkeypatch.setenv(f"{_ENV_PREFIX}_EMBEDDING_PROVIDER", "fastembed")
    for name in NOTES:
        (built / name).unlink()
    result = CliRunner().invoke(app, ["index", "--force"])
    assert result.exit_code == 0, result.output
    vault = _vault(tmp_path)
    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        assert _state(vault) == (set(), set())
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


def test_a_refused_reindex_says_so(tmp_path: Path, built: Path) -> None:
    for name in NOTES:
        (built / name).unlink()
    vault = _vault(tmp_path)
    try:
        vault.index.build_index()
        result = vault.index.reindex()
        assert result.vault_root_empty is True
        assert result.unchanged == 0
    finally:
        vault.close()


def test_the_reindex_command_fails_over_an_empty_vault_folder(
    tmp_path: Path, built: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import _ENV_PREFIX, app

    monkeypatch.setenv(f"{_ENV_PREFIX}_SOURCE_DIR", str(built))
    monkeypatch.setenv(f"{_ENV_PREFIX}_INDEX_PATH", str(tmp_path / "index.db"))
    monkeypatch.delenv(f"{_ENV_PREFIX}_EMBEDDING_PROVIDER", raising=False)
    for name in NOTES:
        (built / name).unlink()
    result = CliRunner().invoke(app, ["reindex"])
    assert result.exit_code == 1
    assert "vault folder is empty" in result.stderr
    assert "Reindex:" not in result.stdout
