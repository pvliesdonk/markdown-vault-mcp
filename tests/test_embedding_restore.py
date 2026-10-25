"""Restoring backups of different ages embeds only what differs (#1505 child 10).

These pin the claims of ``docs/use/embedding-state.md``.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from markdown_vault_mcp.cli import _ENV_PREFIX, app
from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.vault import Vault
from tests.conftest import MockEmbeddingProvider
from tests.fixtures.sidecar_v5_1.notes import NOTES

FIXTURE = Path(__file__).parent / "fixtures" / "sidecar_v5_1"
EDITED = ("beta.md", "# Beta\n\nFerries cross the harbour at dawn.\n")
STORES = ["numpy", "sqlite"]


class _Counting(MockEmbeddingProvider):
    """``mock/mock-dim-32``, counting embedded texts across a test."""

    texts: list[str] = []  # noqa: RUF012 - shared by every instance a run builds

    def embed(self, texts: list[str]) -> list[list[float]]:
        _Counting.texts.extend(texts)
        return super().embed(texts)


@pytest.fixture(autouse=True)
def _fresh_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_Counting, "texts", [])


def _write_notes(tmp_path: Path, notes: dict[str, str] = NOTES) -> None:
    for name, text in notes.items():
        path = tmp_path / "vault" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _edit(tmp_path: Path) -> None:
    name, text = EDITED
    (tmp_path / "vault" / name).write_text(text)


def _run(tmp_path: Path, store: str) -> Vault:
    """Start a vault, catch its index and embeddings up as a start does, stop it."""
    vault = Vault(
        source_dir=tmp_path / "vault",
        settings=VaultSettings(
            index_path=tmp_path / "index.db", read_only=False, embedding_store=store
        ),
        embedding_provider=_Counting(),
    )
    try:
        vault.index.build_index()  # a no-op once the index holds notes
        vault.index.reindex()
        vault.index.build_embeddings()
    finally:
        vault.close()
    return vault


def _store_files(folder: Path) -> list[Path]:
    return [
        p
        for p in folder.glob("index-db-embeddings*")
        if p.suffix != ".lock" and ".corrupt-" not in p.name
    ]


def _back_up(files: list[Path], to: Path) -> None:
    to.mkdir()
    for path in files:
        shutil.copy2(path, to / path.name)


def _put_back(backup: Path, folder: Path, prefix: str) -> None:
    for path in folder.glob(f"{prefix}*"):
        if path.suffix != ".lock":
            path.unlink()
    for path in backup.iterdir():
        shutil.copy2(path, folder / path.name)


def _only_the_edited_note_was_embedded() -> bool:
    return bool(_Counting.texts) and all("Ferries" in t for t in _Counting.texts)


@pytest.mark.parametrize("store", STORES)
def test_an_older_store_backup_embeds_only_the_notes_changed_since(
    tmp_path: Path, store: str
) -> None:
    _write_notes(tmp_path)
    _run(tmp_path, store)
    _back_up(_store_files(tmp_path), tmp_path / "backup")
    _edit(tmp_path)
    _run(tmp_path, store)
    _put_back(tmp_path / "backup", tmp_path, "index-db-embeddings")
    _Counting.texts.clear()
    _run(tmp_path, store)
    assert _only_the_edited_note_was_embedded()


@pytest.mark.parametrize("store", STORES)
def test_an_older_index_db_with_a_newer_store_embeds_nothing(
    tmp_path: Path, store: str
) -> None:
    _write_notes(tmp_path)
    _run(tmp_path, store)
    _back_up([tmp_path / "index.db"], tmp_path / "backup")  # complete after close
    _edit(tmp_path)
    _run(tmp_path, store)
    _put_back(tmp_path / "backup", tmp_path, "index.db")
    _Counting.texts.clear()
    vault = _run(tmp_path, store)
    assert _Counting.texts == []
    del vault
    reopened = Vault(
        source_dir=tmp_path / "vault",
        settings=VaultSettings(index_path=tmp_path / "index.db", read_only=False),
    )
    try:
        hits = reopened.reader.search("ferries", mode="keyword")
        assert [h.path for h in hits] == ["beta.md"]  # the index caught up
    finally:
        reopened.close()


@pytest.mark.parametrize("store", STORES)
def test_a_store_restored_without_index_db_embeds_nothing(
    tmp_path: Path, store: str
) -> None:
    _write_notes(tmp_path)
    _run(tmp_path, store)
    for path in tmp_path.glob("index.db*"):
        path.unlink()
    _Counting.texts.clear()
    _run(tmp_path, store)
    assert _Counting.texts == []


@pytest.mark.parametrize("store", STORES)
def test_upgrading_again_after_a_downgrade_embeds_only_changed_notes(
    tmp_path: Path, store: str, caplog: pytest.LogCaptureFixture
) -> None:
    for suffix in (".json", ".npy"):
        shutil.copy(
            FIXTURE / f"index-db-embeddings{suffix}",
            tmp_path / f"index-db-embeddings{suffix}",
        )
    _write_notes(tmp_path)
    _run(tmp_path, store)  # the upgrade: the new store is now active
    assert _Counting.texts == []
    _edit(tmp_path)  # a note changed while the older release ran
    with caplog.at_level(logging.INFO):
        _run(tmp_path, store)
    assert _only_the_edited_note_was_embedded()
    assert not [
        r for r in caplog.records if r.msg.startswith("embeddings_upgrade_started")
    ]


def _sums(files: list[Path]) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files
        if not p.name.endswith(("-wal", "-shm"))
    }


@pytest.mark.parametrize("store", STORES)
def test_a_restore_through_import_from_path_embeds_nothing(
    tmp_path: Path, store: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.providers as providers_mod

    _write_notes(tmp_path)
    _run(tmp_path, store)
    backup = tmp_path / "backup"
    _back_up(_store_files(tmp_path), backup)
    for path in _store_files(tmp_path):
        path.unlink()
    before = _sums(list(backup.iterdir()))
    _Counting.texts.clear()
    monkeypatch.setattr(providers_mod, "get_embedding_provider", lambda _c: _Counting())
    env: dict[str, Any] = {
        "SOURCE_DIR": tmp_path / "vault",
        "INDEX_PATH": tmp_path / "index.db",
        "EMBEDDING_PROVIDER": "fastembed",
        "EMBEDDING_STORE": store,
    }
    for key, value in env.items():
        monkeypatch.setenv(f"{_ENV_PREFIX}_{key}", str(value))
    monkeypatch.delenv(f"{_ENV_PREFIX}_EMBEDDINGS_PATH", raising=False)
    result = CliRunner().invoke(
        app,
        [
            "embeddings",
            "import",
            "--from",
            store,
            "--from-path",
            str(backup / "index-db-embeddings"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert _Counting.texts == []
    assert _store_files(tmp_path)  # the configured store holds the vectors again
    assert _sums(list(backup.iterdir())) == before  # the backup is only read


@pytest.mark.parametrize("store", STORES)
def test_a_forced_index_rebuild_keeps_the_embeddings(
    tmp_path: Path, store: str
) -> None:
    _write_notes(tmp_path)
    _run(tmp_path, store)
    _Counting.texts.clear()
    vault = Vault(
        source_dir=tmp_path / "vault",
        settings=VaultSettings(
            index_path=tmp_path / "index.db", read_only=False, embedding_store=store
        ),
        embedding_provider=_Counting(),
    )
    try:
        vault.index.build_index(force=True)  # what `reindex --force` runs
        vault.index.reindex()
        vault.index.build_embeddings()
    finally:
        vault.close()
    assert _Counting.texts == []
