"""Observation routes retain evidence of server writes and removals."""

import hashlib
from pathlib import Path

import pytest

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
        vault._coordinator.prepare_index_read()
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
        vault._coordinator.prepare_index_read()
        rows = vault._index_mgr.observations.rows()
        assert rows["new.md"].presence == "removed"
        assert rows["new.md"].moved_to == ".hidden/new.md"
        assert rows[".hidden/new.md"].moved_from == "new.md"
    finally:
        vault.close()


def test_failed_save_keeps_write_evidence_for_retry(
    tmp_path: Path, monkeypatch
) -> None:
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


def test_dot_paths_use_canonical_observation_identity(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr.write("./a.md", "hello")
        vault._coordinator.prepare_index_read()
        assert vault._index_mgr.observations.rows()["a.md"].origin == "server_write"
        vault._doc_mgr.delete("./a.md")
        vault._coordinator.prepare_index_read()
        assert vault._index_mgr.observations.rows()["a.md"].presence == "removed"
    finally:
        vault.close()


def test_unavailable_root_does_not_prove_removal(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    vault = Vault(source_dir=source)
    try:
        vault.index.build_index()
        source.rename(tmp_path / "offline")
        vault._index_mgr.observations.refresh("a.md")
        assert vault._index_mgr.observations.rows()["a.md"].presence == "present"
    finally:
        vault.close()


def test_sweep_discards_contradicted_write_hint(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault._doc_mgr.write("a.md", "server")
        (source / "a.md").write_text("external")
        ledger = vault._index_mgr.observations
        ledger.sweep()
        (source / "a.md").write_text("server")
        ledger.refresh("a.md")
        assert ledger.rows()["a.md"].origin == "unknown"
    finally:
        vault.close()


def test_ledger_failure_does_not_stop_search_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault._doc_mgr.write("a.md", "hello")
        ledger = vault._index_mgr.observations

        def fail(_rows: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(ledger, "_save", fail)
        with pytest.raises(OSError, match="disk full"):
            vault._index_mgr.process_dirty_paths({"a.md"})
        assert vault._fts.get_note("a.md") is not None
        vault._doc_mgr.delete("a.md")
        assert not (source / "a.md").is_file()
    finally:
        vault.close()


def test_folder_move_batches_ledger_and_canonicalizes_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "old").mkdir()
    for i in range(5):
        (source / "old" / f"{i}.md").write_text(str(i))
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        ledger = vault._index_mgr.observations
        save = ledger._save
        calls: list[int] = []

        def record(rows: object) -> None:
            calls.append(1)
            save(rows)  # type: ignore[arg-type]

        monkeypatch.setattr(ledger, "_save", record)
        vault._doc_mgr.move_folder("./old", "new")
        assert calls == []
        vault._index_mgr.process_dirty_paths(
            {f"{folder}/{i}.md" for folder in ("old", "new") for i in range(5)}
        )
        assert len(calls) == 1
        rows = ledger.rows()
        assert rows["old/0.md"].moved_to == "new/0.md"
        assert rows["new/0.md"].moved_from == "old/0.md"
    finally:
        vault.close()


def test_blocked_source_does_not_link_old_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    note = source / "a.md"
    note.write_text("old")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        note.unlink()
        ledger = vault._index_mgr.observations
        ledger.sweep()
        note.write_text("new")
        inspector = ledger._inspector
        original = inspector.observe_path

        def blocked(path: str, visit: object) -> str:
            if path == "a.md":
                return "blocked"
            return original(path, visit)  # type: ignore[arg-type]

        monkeypatch.setattr(inspector, "observe_path", blocked)
        vault._doc_mgr.rename("a.md", "b.md", update_links=False)
        vault._coordinator.prepare_index_read()
        rows = ledger.rows()
        assert rows["a.md"].moved_to is None
        assert rows["b.md"].moved_from is None
    finally:
        vault.close()


def test_newer_ledger_error_does_not_prevent_indexing(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import StoreUnsupportedSchemaError

    source = tmp_path / "vault"
    source.mkdir()
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        (tmp_path / "state.observations.json").write_text(
            '{"version": 999, "rows": {}}'
        )
        vault._doc_mgr.write("a.md", "hello")
        with pytest.raises(StoreUnsupportedSchemaError):
            vault._index_mgr.process_dirty_paths({"a.md"})
        assert vault._fts.get_note("a.md") is not None
    finally:
        vault.close()


def test_write_hint_does_not_wait_for_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    source = tmp_path / "vault"
    source.mkdir()
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    entered, release, written = threading.Event(), threading.Event(), threading.Event()
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        inspector = vault._index_mgr.observations._inspector
        original = inspector.observe_files

        def held(visit):
            entered.set()
            assert release.wait(5)
            return original(visit)

        monkeypatch.setattr(inspector, "observe_files", held)
        sweep = threading.Thread(target=vault._index_mgr.observations.sweep)
        sweep.start()
        assert entered.wait(5)

        def write() -> None:
            vault._doc_mgr.write("a.md", "hello")
            written.set()

        writer = threading.Thread(target=write)
        writer.start()
        assert written.wait(1)
    finally:
        release.set()
        sweep.join(timeout=5)
        writer.join(timeout=5)
        vault.close()


def test_move_schedules_search_and_callback_when_ledger_save_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.index.build_index()
        dirty: set[str] = set()
        callbacks: list[str] = []
        vault._doc_mgr._mark_paths_dirty = dirty.update
        monkeypatch.setattr(
            vault._doc_mgr._notifier,
            "fire_rename",
            lambda *_args: callbacks.append("rename"),
        )
        ledger = vault._index_mgr.observations

        def fail(_rows: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(ledger, "_save", fail)
        vault._doc_mgr.rename("a.md", "b.md", update_links=False)
        assert callbacks == ["rename"]
        with pytest.raises(OSError, match="disk full"):
            vault._index_mgr.process_dirty_paths(dirty)
        assert vault._fts.get_note("b.md") is not None
        assert vault._fts.get_note("a.md") is None
    finally:
        vault.close()


def test_sweep_discards_hint_for_disappeared_file(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault._doc_mgr.write("a.md", "server")
        (source / "a.md").unlink()
        ledger = vault._index_mgr.observations
        ledger.sweep()
        (source / "a.md").write_text("server")
        ledger.refresh("a.md")
        assert ledger.rows()["a.md"].origin == "unknown"
    finally:
        vault.close()


def test_single_path_observation_refuses_internal_and_nonfiles(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / ".git").mkdir()
    (source / "folder.md").mkdir()
    vault = Vault(source_dir=source)
    try:
        inspector = vault._index_mgr.observations._inspector
        assert inspector.observe_path("a.txt", lambda *_args: None) == "blocked"
        assert inspector.observe_path(".git/a.md", lambda *_args: None) == "blocked"
        assert inspector.observe_path("folder.md", lambda *_args: None) == "blocked"
    finally:
        vault.close()


def test_invalid_yaml_move_still_schedules_search_and_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml

    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("---\nbroken: [\n---\nbody")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        dirty: set[str] = set()
        callbacks: list[str] = []
        vault._doc_mgr._mark_paths_dirty = dirty.update
        monkeypatch.setattr(
            vault._doc_mgr._notifier,
            "fire_rename",
            lambda *_args: callbacks.append("rename"),
        )
        with pytest.raises(yaml.YAMLError):
            vault._doc_mgr.rename("a.md", "b.md", update_links=False)
        assert callbacks == ["rename"]
        assert dirty == {"a.md", "b.md"}
        vault._index_mgr.process_dirty_paths(dirty)
        rows = vault._index_mgr.observations.rows()
        assert rows["a.md"].moved_to == "b.md"
        assert rows["b.md"].moved_from == "a.md"
    finally:
        vault.close()


def test_resolved_alias_routes_to_real_identity(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "real" / "sub").mkdir(parents=True)
    (source / "alias").symlink_to(source / "real" / "sub", target_is_directory=True)
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr.write("alias/../a.md", "hello")
        vault._coordinator.prepare_index_read()
        rows = vault._index_mgr.observations.rows()
        assert rows["real/a.md"].origin == "server_write"
        assert "a.md" not in rows
        vault._doc_mgr.delete("alias/../a.md")
        vault._coordinator.prepare_index_read()
        assert vault._index_mgr.observations.rows()["real/a.md"].presence == "removed"
    finally:
        vault.close()


@pytest.mark.parametrize(
    "data", ['{"version": "1", "rows": {}}', '{"version": 1, "rows": []}']
)
def test_malformed_ledger_rebuilds_history(tmp_path: Path, data: str) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    state = tmp_path / "state"
    ledger_path = tmp_path / "state.observations.json"
    ledger_path.write_text(data)
    vault = Vault(source_dir=source, settings=VaultSettings(state_path=state))
    try:
        vault.index.build_index()
        assert vault._index_mgr.observations.rows()["a.md"].presence == "present"
    finally:
        vault.close()
