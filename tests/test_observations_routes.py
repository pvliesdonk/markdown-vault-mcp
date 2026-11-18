"""Observation routes retain evidence of server writes and removals."""

import hashlib
from pathlib import Path

from markdown_vault_mcp.vault import Vault, VaultSettings


def test_write_and_delete_update_observations(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault._doc_mgr.write("a.md", "hello")
        vault._index_mgr.process_dirty_paths({"a.md"})
        row = vault._index_mgr.observations.rows()["a.md"]
        assert (
            row.content_digest
            == hashlib.sha256((source / "a.md").read_bytes()).hexdigest()
        )
        assert row.origin == "server_write"
        vault._doc_mgr.delete("a.md")
        vault._index_mgr.process_dirty_paths({"a.md"})
        row = vault._index_mgr.observations.rows()["a.md"]
        assert row.presence == "removed"
        assert row.removed_observed_at is not None
    finally:
        vault.close()


def test_external_overwrite_before_dirty_processing(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault._doc_mgr.write("a.md", "server")
        (source / "a.md").write_text("external")
        vault._index_mgr.process_dirty_paths({"a.md"})
        row = vault._index_mgr.observations.rows()["a.md"]
        assert row.content_digest == hashlib.sha256(b"external").hexdigest()
        assert row.origin == "unknown"
    finally:
        vault.close()


def test_rename_records_both_paths_and_scan_does_not_guess_move(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr.rename("a.md", "b.md", update_links=False)
        rows = vault._index_mgr.observations.rows()
        assert rows["a.md"].presence == "removed"
        assert rows["a.md"].moved_to == "b.md"
        assert rows["b.md"].moved_from == "a.md"
        (source / "b.md").rename(source / "c.md")
        vault.index.reindex()
        rows = vault._index_mgr.observations.rows()
        assert rows["b.md"].presence == "removed"
        assert rows["c.md"].moved_from is None
    finally:
        vault.close()


def test_rename_unobserved_source_to_hidden_excluded_path(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / ".hidden").mkdir()
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, exclude_patterns=[".hidden/**"]),
    )
    try:
        vault.index.build_index()
        (source / "new.md").write_text("unobserved")
        vault._doc_mgr.rename("new.md", ".hidden/new.md", update_links=False)
        rows = vault._index_mgr.observations.rows()
        assert rows["new.md"].presence == "removed"
        assert rows["new.md"].moved_to == ".hidden/new.md"
        assert rows[".hidden/new.md"].moved_from == "new.md"
    finally:
        vault.close()


def test_failed_save_keeps_write_evidence_for_retry(
    tmp_path: Path, monkeypatch
) -> None:
    import pytest

    source = tmp_path / "vault"
    source.mkdir()
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault._doc_mgr.write("a.md", "server")
        ledger = vault._index_mgr.observations
        save = ledger._save

        def fail(_rows):
            raise OSError("disk full")

        monkeypatch.setattr(ledger, "_save", fail)
        with pytest.raises(OSError, match="disk full"):
            ledger.refresh("a.md")
        monkeypatch.setattr(ledger, "_save", save)
        ledger.refresh("a.md")
        first = ledger.rows()["a.md"]
        assert first.origin == "server_write"
        ledger.refresh("a.md")
        ledger.sweep()
        assert ledger.rows()["a.md"] == first
    finally:
        vault.close()


def test_delete_unobserved_source_retains_removal(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        (source / "new.md").write_text("unobserved")
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault._doc_mgr.delete("new.md")
        vault._index_mgr.process_dirty_paths({"new.md"})
        assert vault._index_mgr.observations.rows()["new.md"].presence == "removed"
    finally:
        vault.close()


def test_version_one_rows_survive_upgrade(tmp_path: Path) -> None:
    import json

    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    state = tmp_path / "state"
    vault = Vault(source_dir=source, settings=VaultSettings(state_path=state))
    try:
        vault.index.build_index()
        ledger_path = tmp_path / "state.observations.json"
        data = json.loads(ledger_path.read_text())
        data["version"] = 1
        for row in data["rows"].values():
            del row["moved_from"]
            del row["moved_to"]
        ledger_path.write_text(json.dumps(data))
        before = vault._index_mgr.observations.rows()["a.md"]
        (source / "b.md").write_text("new")
        vault.index.reindex()
        assert vault._index_mgr.observations.rows()["a.md"] == before
        assert json.loads(ledger_path.read_text())["version"] == 2
    finally:
        vault.close()


def test_reindex_after_restart_repairs_interrupted_write(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("before")
    settings = VaultSettings(state_path=tmp_path / "state")
    vault = Vault(source_dir=source, settings=settings)
    try:
        vault.index.build_index()
        before = vault._index_mgr.observations.rows()["a.md"]
        # Disk replacement happened, but the process exited before ledger update.
        (source / "a.md").write_text("after")
    finally:
        vault.close()
    reopened = Vault(source_dir=source, settings=settings)
    try:
        reopened._index_mgr.reindex()
        after = reopened._index_mgr.observations.rows()["a.md"]
        assert after.content_digest == hashlib.sha256(b"after").hexdigest()
        assert after.first_seen_at == before.first_seen_at
        assert after.origin == "unknown"
    finally:
        reopened.close()
