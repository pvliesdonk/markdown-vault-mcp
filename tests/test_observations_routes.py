"""Observation routes retain evidence of server writes and removals."""

import hashlib
from pathlib import Path

import pytest

from markdown_vault_mcp.vault import Vault, VaultSettings


@pytest.mark.parametrize("succeed", [0, 1, 2, 3])
def test_interrupted_folder_move_records_completed_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, succeed: int
) -> None:
    from markdown_vault_mcp.exceptions import FolderMoveInterruptedError
    from markdown_vault_mcp.managers import document

    source = tmp_path / "vault"
    (source / "old").mkdir(parents=True)
    for name in ("a.md", "b.md", "c.md"):
        (source / "old" / name).write_text(name)
    (source / "old" / "asset.txt").write_text("asset")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        dirty: list[str] = []
        vault._doc_mgr._mark_paths_dirty = dirty.extend
        original_move = document.shutil.move
        plan = vault._doc_mgr._plan_folder_move
        completed: list[str] = []

        def non_note_first(*args, **kwargs):
            moves, md_map, others = plan(*args, **kwargs)
            moves.sort(key=lambda pair: pair[0].suffix == ".md")
            return moves, md_map, others

        def interrupted(src: str, dst: str) -> str:
            if len(completed) == succeed:
                raise OSError("disk failure")
            result = original_move(src, dst)
            completed.append(Path(src).name)
            return result

        monkeypatch.setattr(document.shutil, "move", interrupted)
        monkeypatch.setattr(vault._doc_mgr, "_plan_folder_move", non_note_first)
        with pytest.raises(FolderMoveInterruptedError):
            vault.writer.move_folder("old", "new")
        assert set(dirty) == {
            f"{folder}/{name}"
            for folder in ("old", "new")
            for name in completed
            if name.endswith(".md")
        }
        vault.index.reindex()
        rows = vault._index_mgr.observations.rows()
        for name in ("a.md", "b.md", "c.md"):
            old = rows[f"old/{name}"]
            if name in completed:
                assert old.presence == "removed"
                assert old.moved_to == f"new/{name}"
                assert rows[f"new/{name}"].moved_from == f"old/{name}"
            else:
                assert old.presence == "present"
                assert old.moved_to is None
                assert f"new/{name}" not in rows
    finally:
        vault.close()


@pytest.mark.parametrize("link", ["[[a]]", "[self](a.md)"])
@pytest.mark.parametrize("overlap", [False, True])
def test_rename_own_rewrite_preserves_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link: str, overlap: bool
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text(f"see {link}\n")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        rewrite = vault._doc_mgr._rewrite_one_source

        def interleaved(*args):
            content = rewrite(*args)
            if overlap:
                vault._index_mgr.process_dirty_paths({"a.md", "b.md"})
            return content

        monkeypatch.setattr(vault._doc_mgr, "_rewrite_one_source", interleaved)
        vault.writer.rename("a.md", "b.md", update_links=True)
        vault._index_mgr.process_dirty_paths({"a.md", "b.md"})
        rows = vault._index_mgr.observations.rows()
        assert rows["a.md"].moved_to == "b.md"
        assert rows["b.md"].moved_from == "a.md"
        if not overlap:
            assert rows["b.md"].origin == "server_write"
    finally:
        vault.close()


def test_folder_move_own_rewrites_preserve_both_pairs(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    (source / "old").mkdir(parents=True)
    (source / "old" / "a.md").write_text("[[old/b]]")
    (source / "old" / "b.md").write_text("[[old/a]]")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault.writer.move_folder("old", "new")
        vault._index_mgr.process_dirty_paths(
            {"old/a.md", "old/b.md", "new/a.md", "new/b.md"}
        )
        rows = vault._index_mgr.observations.rows()
        for name in ("a.md", "b.md"):
            assert rows[f"old/{name}"].moved_to == f"new/{name}"
            assert rows[f"new/{name}"].moved_from == f"old/{name}"
            assert rows[f"new/{name}"].origin == "server_write"
    finally:
        vault.close()


@pytest.mark.parametrize("recreate", [False, True])
def test_external_change_after_rewritten_move_breaks_relation(
    tmp_path: Path, recreate: bool
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("[[a]]")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault.writer.rename("a.md", "b.md", update_links=True)
        target = source / "b.md"
        if recreate:
            target.unlink()
        target.write_text("external")
        vault._index_mgr.process_dirty_paths({"a.md", "b.md"})
        rows = vault._index_mgr.observations.rows()
        assert rows["a.md"].moved_to is None
        assert rows["b.md"].moved_from is None
        assert rows["b.md"].origin == "unknown"
    finally:
        vault.close()


@pytest.mark.parametrize("external_between", [False, True])
def test_chain_after_rewrite_retains_only_proven_links(
    tmp_path: Path, external_between: bool
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("[[a]]")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault.writer.rename("a.md", "b.md", update_links=True)
        if external_between:
            (source / "b.md").write_text("external")
        vault.writer.rename("b.md", "c.md", update_links=False)
        vault._index_mgr.process_dirty_paths({"a.md", "b.md", "c.md"})
        rows = vault._index_mgr.observations.rows()
        assert rows["a.md"].moved_to == (None if external_between else "b.md")
        assert rows["b.md"].moved_from == (None if external_between else "a.md")
        assert rows["b.md"].moved_to == "c.md"
        assert rows["c.md"].moved_from == "b.md"
    finally:
        vault.close()


def test_rewritten_move_uses_live_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("[[a]]")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        rewrite = vault._doc_mgr._rewrite_one_source

        def interleaved(*args):
            content = rewrite(*args)
            mark_dirty = vault._doc_mgr._mark_paths_dirty
            assert mark_dirty is not None
            mark_dirty(["a.md", "b.md"])
            vault._coordinator.prepare_index_read()
            return content

        monkeypatch.setattr(vault._doc_mgr, "_rewrite_one_source", interleaved)
        vault.writer.rename("a.md", "b.md", update_links=True)
        vault._coordinator.prepare_index_read()
        rows = vault._index_mgr.observations.rows()
        assert rows["a.md"].moved_to == "b.md"
        assert rows["b.md"].moved_from == "a.md"
    finally:
        vault.close()


def test_failed_rewrite_does_not_claim_server_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.managers import document

    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("[[a]]")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None

        def fail_write(*_args, **_kwargs):
            raise OSError("rewrite failed")

        monkeypatch.setattr(document, "atomic_write", fail_write)
        vault.writer.rename("a.md", "b.md", update_links=True)
        vault._index_mgr.process_dirty_paths({"a.md", "b.md"})
        rows = vault._index_mgr.observations.rows()
        assert rows["a.md"].moved_to == "b.md"
        assert rows["b.md"].moved_from == "a.md"
        assert rows["b.md"].origin == "unknown"
    finally:
        vault.close()


def test_sweep_does_not_reprobe_unchanged_tombstones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    note = source / "a.md"
    note.write_text("hello")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        ledger = vault._index_mgr.observations
        note.unlink()
        ledger.sweep()
        removed = ledger.rows()["a.md"]
        observe = ledger._inspector.observe_path
        probed: list[str] = []

        def record(path, visit):
            probed.append(path)
            return observe(path, visit)

        monkeypatch.setattr(ledger._inspector, "observe_path", record)
        ledger.sweep()
        assert "a.md" not in probed
        assert ledger.rows()["a.md"] == removed
    finally:
        vault.close()


@pytest.mark.parametrize("drain_between", [False, True])
@pytest.mark.parametrize("finish", ["move", "delete"])
def test_chained_mutations_preserve_intermediate_move(
    tmp_path: Path, drain_between: bool, finish: str
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault.writer.rename("a.md", "b.md", update_links=False)
        if drain_between:
            vault._index_mgr.process_dirty_paths({"a.md", "b.md"})
        if finish == "move":
            vault.writer.rename("b.md", "c.md", update_links=False)
        else:
            vault.writer.delete("b.md")
        vault._index_mgr.process_dirty_paths({"a.md", "b.md", "c.md"})
        rows = vault._index_mgr.observations.rows()
        assert rows["a.md"].moved_to == "b.md"
        assert rows["b.md"].moved_from == "a.md"
        assert rows["b.md"].moved_to == ("c.md" if finish == "move" else None)
        if finish == "move":
            assert rows["c.md"].moved_from == "b.md"
    finally:
        vault.close()


def test_recreated_move_destination_does_not_inherit_relation(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("original")
    (source / "anchor.md").write_text("anchor")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault.writer.rename("a.md", "b.md", update_links=False)
        vault.writer.delete("b.md")
        vault.writer.write("b.md", "replacement")
        vault._index_mgr.process_dirty_paths({"a.md", "b.md"})
        rows = vault._index_mgr.observations.rows()
        assert rows["a.md"].moved_to is None
        assert rows["b.md"].moved_from is None
    finally:
        vault.close()


def test_recreated_source_and_rename_back_use_latest_state(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("original")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault.writer.rename("a.md", "b.md", update_links=False)
        vault.writer.rename("b.md", "a.md", update_links=False)
        vault._index_mgr.process_dirty_paths({"a.md", "b.md"})
        rows = vault._index_mgr.observations.rows()
        assert rows["a.md"].moved_from == "b.md"
        assert rows["a.md"].moved_to is None
        assert rows["b.md"].moved_to == "a.md"
        vault.writer.write("b.md", "replacement")
        vault._index_mgr.process_dirty_paths({"b.md"})
        rows = vault._index_mgr.observations.rows()
        assert rows["b.md"].moved_to is None
        assert rows["b.md"].moved_from is None
    finally:
        vault.close()


def test_public_writer_refreshes_observations_async(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault.writer.write("a.md", "hello")
        vault._coordinator.prepare_index_read()
        assert vault._index_mgr.observations.rows()["a.md"].origin == "server_write"
        vault.writer.rename("a.md", "b.md", update_links=False)
        vault._coordinator.prepare_index_read()
        rows = vault._index_mgr.observations.rows()
        assert rows["a.md"].moved_to == "b.md"
        assert rows["b.md"].moved_from == "a.md"
    finally:
        vault.close()


def test_persistent_cache_reloads_other_instance_and_external_replace(
    tmp_path: Path,
) -> None:
    import json

    from markdown_vault_mcp.indexing.observations import ObservationLedger

    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("one")
    vault = Vault(
        source_dir=source, settings=VaultSettings(state_path=tmp_path / "state")
    )
    try:
        vault.index.build_index()
        first = vault._index_mgr.observations
        path = tmp_path / "state.observations.json"
        second = ObservationLedger(path, first._inspector)
        original = first.rows()["a.md"]
        (source / "a.md").write_text("two")
        second.refresh_many({"a.md"})
        assert first.rows()["a.md"].content_digest != original.content_digest
        replacement = tmp_path / "replacement.json"
        replacement.write_text(json.dumps({"version": 2, "rows": {}}))
        replacement.replace(path)
        assert first.rows() == {}
        path.write_text("malformed")
        assert first.rows() == {}
    finally:
        vault.close()


def test_save_does_not_cache_rows_for_replaced_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("one")
    vault = Vault(
        source_dir=source, settings=VaultSettings(state_path=tmp_path / "state")
    )
    try:
        vault.index.build_index()
        ledger = vault._index_mgr.observations
        path = tmp_path / "state.observations.json"
        replacement = tmp_path / "external.json"
        replacement.write_text('{"version": 2, "rows": {}}')
        original_replace = Path.replace

        def replace_then_external(staged: Path, target: Path) -> Path:
            result = original_replace(staged, target)
            if target == path:
                original_replace(replacement, path)
            return result

        monkeypatch.setattr(Path, "replace", replace_then_external)
        (source / "a.md").write_text("two")
        ledger.refresh_many({"a.md"})
        assert ledger.rows() == {}
    finally:
        vault.close()


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
            ledger.refresh_many({"a.md"})
        monkeypatch.setattr(ledger, "_save", save)
        ledger.refresh_many({"a.md"})
        first = ledger.rows()["a.md"]
        assert first.origin == "server_write"
        ledger.refresh_many({"a.md"})
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
        vault._index_mgr.observations.refresh_many({"a.md"})
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
        ledger.refresh_many({"a.md"})
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
        vault._index_mgr.process_dirty_paths({"a.md"})
        assert vault._fts.get_note("a.md") is not None
        with pytest.raises(StoreUnsupportedSchemaError):
            vault.index.reindex()
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
        ledger.refresh_many({"a.md"})
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
        assert callbacks == []
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


@pytest.mark.parametrize("operation", ["delete", "rename"])
@pytest.mark.parametrize("sweep_first", [False, True])
def test_capture_preserves_path_history(
    tmp_path: Path, operation: str, sweep_first: bool
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    note = source / "a.md"
    note.write_text("first")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        ledger = vault._index_mgr.observations
        first = ledger.rows()["a.md"].first_seen_at
        note.write_text("external change")
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        if operation == "delete":
            vault._doc_mgr.delete("a.md")
        else:
            vault._doc_mgr.rename("a.md", "b.md", update_links=False)
        if sweep_first:
            ledger.sweep()
            removed_at = ledger.rows()["a.md"].removed_observed_at
        ledger.refresh_many({"a.md", "b.md"})
        row = ledger.rows()["a.md"]
        assert row.first_seen_at == first
        assert row.content_digest == hashlib.sha256(b"external change").hexdigest()
        if sweep_first:
            assert row.removed_observed_at == removed_at
    finally:
        vault.close()


@pytest.mark.parametrize("blocked_path", ["a.md", "b.md"])
@pytest.mark.parametrize("recovery", ["refresh", "sweep"])
def test_blocked_move_pair_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocked_path: str, recovery: str
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("hello")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault._doc_mgr.rename("a.md", "b.md", update_links=False)
        ledger = vault._index_mgr.observations
        inspector = ledger._inspector
        original = inspector.observe_path

        def blocked(path: str, visit: object) -> str:
            if path == blocked_path:
                return "blocked"
            return original(path, visit)  # type: ignore[arg-type]

        monkeypatch.setattr(inspector, "observe_path", blocked)
        ledger.refresh_many({"a.md", "b.md"})
        monkeypatch.setattr(inspector, "observe_path", original)
        if recovery == "refresh":
            ledger.refresh_many({"b.md"})
        else:
            ledger.sweep()
        rows = ledger.rows()
        assert rows["a.md"].moved_to == "b.md"
        assert rows["b.md"].moved_from == "a.md"
    finally:
        vault.close()


def test_async_ledger_outage_does_not_gate_search_or_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "seed.md").write_text("seed")
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.index.build_index()
        ledger = vault._index_mgr.observations

        def fail(_rows: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(ledger, "_save", fail)
        vault._doc_mgr.write("a.md", "hello")
        vault._coordinator.prepare_index_read()
        assert vault._fts.get_note("a.md") is not None
        vault._doc_mgr.rename("seed.md", "renamed.md", update_links=True)
        vault._coordinator.prepare_index_read()
        assert vault._fts.get_note("renamed.md") is not None
        assert any(
            record.msg == "observation_dirty_refresh_failed"
            for record in caplog.records
        )
        with pytest.raises(OSError, match="disk full"):
            vault.index.reindex()
    finally:
        vault.close()


def test_sweep_started_before_capture_preserves_later_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("before")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    entered, release = threading.Event(), threading.Event()
    worker = None
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        ledger = vault._index_mgr.observations
        first = ledger.rows()["a.md"].first_seen_at
        inspector = ledger._inspector
        original = inspector.observe_files

        def held(visit):
            entered.set()
            assert release.wait(5)
            return original(visit)

        monkeypatch.setattr(inspector, "observe_files", held)
        worker = threading.Thread(target=ledger.sweep)
        worker.start()
        assert entered.wait(5)
        vault._doc_mgr.delete("a.md")
        release.set()
        worker.join(timeout=5)
        before = ledger.rows()["a.md"]
        ledger.refresh_many({"a.md"})
        after = ledger.rows()["a.md"]
        assert after.first_seen_at == first
        assert after.removed_observed_at == before.removed_observed_at
    finally:
        release.set()
        if worker is not None:
            worker.join(timeout=5)
        vault.close()


def test_failed_move_save_recovers_pair_on_sweep(
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
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault._doc_mgr.rename("a.md", "b.md", update_links=False)
        ledger = vault._index_mgr.observations
        save = ledger._save

        def fail(_rows: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(ledger, "_save", fail)
        with pytest.raises(OSError, match="disk full"):
            ledger.refresh_many({"a.md", "b.md"})
        monkeypatch.setattr(ledger, "_save", save)
        ledger.sweep()
        rows = ledger.rows()
        assert rows["a.md"].moved_to == "b.md"
        assert rows["b.md"].moved_from == "a.md"
    finally:
        vault.close()


def test_move_without_current_source_does_not_link_old_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    note = source / "a.md"
    note.write_text("old")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        ledger = vault._index_mgr.observations
        note.unlink()
        ledger.sweep()
        note.write_text("new")
        monkeypatch.setattr(ledger, "capture", lambda _path: None)
        vault._doc_mgr.rename("a.md", "b.md", update_links=False)
        vault._coordinator.prepare_index_read()
        rows = ledger.rows()
        assert rows["a.md"].moved_to is None
        assert rows["b.md"].moved_from == "a.md"
    finally:
        vault.close()


def test_move_folder_resolved_alias_records_real_paths(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "real" / "sub").mkdir(parents=True)
    (source / "real" / "old").mkdir()
    (source / "real" / "old" / "a.md").write_text("hello")
    (source / "alias").symlink_to(source / "real" / "sub", target_is_directory=True)
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr.move_folder("alias/../old", "new")
        vault._coordinator.prepare_index_read()
        rows = vault._index_mgr.observations.rows()
        assert rows["real/old/a.md"].moved_to == "new/a.md"
        assert rows["new/a.md"].moved_from == "real/old/a.md"
    finally:
        vault.close()


def test_sweep_preserves_new_same_digest_write_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault._doc_mgr.write("a.md", "server")
        (source / "a.md").write_text("external")
        ledger = vault._index_mgr.observations
        original = ledger._inspector.observe_files

        def interleaved(visit):
            blocked = original(visit)
            vault._doc_mgr.write("a.md", "server")
            return blocked

        monkeypatch.setattr(ledger._inspector, "observe_files", interleaved)
        ledger.sweep()
        ledger.refresh_many({"a.md"})
        assert ledger.rows()["a.md"].origin == "server_write"
    finally:
        vault.close()


def test_sweep_rechecks_absence_after_directory_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    note = source / "a.md"
    note.write_text("old")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        ledger = vault._index_mgr.observations
        note.unlink()
        original = ledger._inspector.observe_files

        def interleaved(visit):
            blocked = original(visit)
            note.write_text("new")
            return blocked

        monkeypatch.setattr(ledger._inspector, "observe_files", interleaved)
        ledger.sweep()
        row = ledger.rows()["a.md"]
        assert row.presence == "present"
        assert row.content_digest == hashlib.sha256(b"new").hexdigest()
    finally:
        vault.close()
