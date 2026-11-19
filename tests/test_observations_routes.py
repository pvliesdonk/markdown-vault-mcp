"""Observation routes retain evidence of server writes and removals."""

import hashlib
from pathlib import Path

import pytest

from markdown_vault_mcp.vault import Vault, VaultSettings


def test_alias_rename_queues_index_and_observation_paths(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    (source / "real" / "sub").mkdir(parents=True)
    (source / "real" / "a.md").write_text("alpha")
    (source / "alias").symlink_to(source / "real" / "sub", target_is_directory=True)
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        dirty: set[str] = set()
        vault._doc_mgr._mark_paths_dirty = dirty.update
        vault.writer.rename("alias/../a.md", "alias/../b.md", update_links=False)
        assert {"alias/../a.md", "alias/../b.md", "real/a.md", "real/b.md"} <= dirty
        vault._index_mgr.process_dirty_paths(dirty)
        rows = vault._index_mgr.observations.rows()
        assert rows["real/a.md"].moved_to == "real/b.md"
        assert rows["real/b.md"].moved_from == "real/a.md"
    finally:
        vault.close()


@pytest.mark.parametrize("destination", ["alias/../new", "new"])
def test_alias_folder_move_queues_completed_caller_and_canonical_paths(
    tmp_path: Path, destination: str
) -> None:
    source = tmp_path / "vault"
    (source / "real" / "sub").mkdir(parents=True)
    (source / "real" / "old").mkdir()
    (source / "real" / "old" / "a.md").write_text("hello")
    (source / "alias").symlink_to(source / "real" / "sub", target_is_directory=True)
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        dirty: set[str] = set()
        vault._doc_mgr._mark_paths_dirty = dirty.update
        vault.writer.move_folder("alias/../old", destination)
        target = "real/new" if destination.startswith("alias") else "new"
        assert {
            "alias/../old/a.md",
            f"{destination}/a.md",
            "real/old/a.md",
            f"{target}/a.md",
        } <= dirty
        vault._index_mgr.process_dirty_paths(dirty)
        rows = vault._index_mgr.observations.rows()
        assert rows["real/old/a.md"].moved_to == f"{target}/a.md"
        assert rows[f"{target}/a.md"].moved_from == "real/old/a.md"
    finally:
        vault.close()


def test_symlink_link_rewrite_records_canonical_write_digest(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "real.md").write_text("[[target]]")
    (source / "link.md").symlink_to("real.md")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        manager = vault._doc_mgr
        callbacks, dirty, failed = manager._rewrite_link_sources(
            {
                "link.md": [
                    (
                        "target.md",
                        {
                            "link_type": "wikilink",
                            "raw_target": "target",
                            "fragment": None,
                        },
                    )
                ]
            },
            {"target.md": "renamed.md"},
            op_name="rename",
        )
        assert callbacks and not failed
        assert "link.md" in dirty
        vault._index_mgr.process_dirty_paths(set(dirty))
        row = vault._index_mgr.observations.rows()["real.md"]
        assert (
            row.content_digest
            == hashlib.sha256((source / "real.md").read_bytes()).hexdigest()
        )
        assert row.origin == "server_write"
    finally:
        vault.close()


@pytest.mark.parametrize("reconcile", ["refresh", "sweep"])
def test_failed_capture_does_not_link_external_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reconcile: str
) -> None:
    from markdown_vault_mcp import bundle_inventory

    source = tmp_path / "vault"
    source.mkdir()
    note = source / "a.md"
    note.write_text("original")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        ledger = vault._index_mgr.observations
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        original = bundle_inventory._observation_digests

        def changed_during_read(stream):
            result = original(stream)
            note.write_text("editor update during capture")
            monkeypatch.setattr(bundle_inventory, "_observation_digests", original)
            return result

        monkeypatch.setattr(
            bundle_inventory, "_observation_digests", changed_during_read
        )
        vault.writer.rename("a.md", "b.md", update_links=False)
        (source / "b.md").write_text("external replacement")
        if reconcile == "refresh":
            vault._index_mgr.process_dirty_paths({"a.md", "b.md"})
        else:
            ledger.sweep()
        rows = ledger.rows()
        assert rows["a.md"].presence == "removed"
        assert rows["a.md"].moved_to is None
        assert rows["b.md"].presence == "present"
        assert rows["b.md"].moved_from is None
    finally:
        vault.close()


def test_write_through_symlink_refreshes_caller_search_path(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "real.md").write_text("alpha")
    (source / "link.md").symlink_to("real.md")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        assert "link.md" in {
            hit.path for hit in vault.reader.search("alpha", mode="keyword")
        }
        vault.writer.write("link.md", "beta")
        vault._coordinator.prepare_index_read()
        assert "link.md" in {
            hit.path for hit in vault.reader.search("beta", mode="keyword")
        }
        assert "link.md" not in {
            hit.path for hit in vault.reader.search("alpha", mode="keyword")
        }
        row = vault._index_mgr.observations.rows()["real.md"]
        assert row.content_digest == hashlib.sha256(b"beta").hexdigest()
        assert row.origin == "server_write"
    finally:
        vault.close()


@pytest.mark.parametrize("alias", [False, True])
@pytest.mark.parametrize("succeed", [0, 1, 2, 3])
def test_interrupted_folder_move_records_completed_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, succeed: int, alias: bool
) -> None:
    from markdown_vault_mcp.exceptions import FolderMoveInterruptedError
    from markdown_vault_mcp.managers import document

    source = tmp_path / "vault"
    root = source / "real" if alias else source
    (root / "old").mkdir(parents=True)
    if alias:
        (root / "sub").mkdir()
        (source / "alias").symlink_to(root / "sub", target_is_directory=True)
    for name in ("a.md", "b.md", "c.md"):
        (root / "old" / name).write_text(name)
    (root / "old" / "asset.txt").write_text("asset")
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
        prefix = "alias/../" if alias else ""
        canonical = "real/" if alias else ""
        with pytest.raises(FolderMoveInterruptedError):
            vault.writer.move_folder(f"{prefix}old", f"{prefix}new")
        assert set(dirty) == {
            f"{base}{folder}/{name}"
            for base in {prefix, canonical}
            for folder in ("old", "new")
            for name in completed
            if name.endswith(".md")
        }
        vault.index.reindex()
        rows = vault._index_mgr.observations.rows()
        for name in ("a.md", "b.md", "c.md"):
            old = rows[f"{canonical}old/{name}"]
            if name in completed:
                assert old.presence == "removed"
                assert old.moved_to == f"{canonical}new/{name}"
                assert (
                    rows[f"{canonical}new/{name}"].moved_from
                    == f"{canonical}old/{name}"
                )
            else:
                assert old.presence == "present"
                assert old.moved_to is None
                assert f"{canonical}new/{name}" not in rows
    finally:
        vault.close()


@pytest.mark.parametrize("overlap", [False, True])
def test_rename_own_rewrite_preserves_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overlap: bool
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("see [[a]]\n")
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


def test_chain_after_rewrite_retains_only_proven_links(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("[[a]]")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault.writer.rename("a.md", "b.md", update_links=True)
        (source / "b.md").write_text("external")
        vault.writer.rename("b.md", "c.md", update_links=False)
        vault._index_mgr.process_dirty_paths({"a.md", "b.md", "c.md"})
        rows = vault._index_mgr.observations.rows()
        assert rows["a.md"].moved_to is None
        assert rows["b.md"].moved_from is None
        assert rows["b.md"].moved_to == "c.md"
        assert rows["c.md"].moved_from == "b.md"
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


@pytest.mark.parametrize("rewrite", [False, True])
@pytest.mark.parametrize("drain_between", [False, True])
@pytest.mark.parametrize("finish", ["move", "delete"])
def test_chained_mutations_preserve_intermediate_move(
    tmp_path: Path, drain_between: bool, finish: str, rewrite: bool
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("[[a]]" if rewrite else "hello")
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault.writer.rename("a.md", "b.md", update_links=rewrite)
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
    (source / "folder").mkdir()
    (source / "a.md").write_text("original")
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


def test_persistent_ledger_reads_external_replace(tmp_path: Path) -> None:
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
        backup = tmp_path / "backup.json"
        backup.write_bytes(path.read_bytes())
        original = ledger.rows()["a.md"]
        (source / "a.md").write_text("two")
        ledger.refresh_many({"a.md"})
        assert ledger.rows()["a.md"].content_digest != original.content_digest
        backup.replace(path)
        assert ledger.rows()["a.md"] == original
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("path", "canonical"),
    [("a.md", "a.md"), ("./a.md", "a.md"), ("alias/../a.md", "real/a.md")],
)
def test_write_and_delete_update_observations(
    tmp_path: Path, path: str, canonical: str
) -> None:
    source = tmp_path / "vault"
    (source / "real" / "sub").mkdir(parents=True)
    (source / "alias").symlink_to(source / "real" / "sub", target_is_directory=True)
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr.write(path, "hello")
        vault._coordinator.prepare_index_read()
        row = vault._index_mgr.observations.rows()[canonical]
        assert row.content_digest == hashlib.sha256(b"hello").hexdigest()
        assert row.origin == "server_write"
        vault._doc_mgr.delete(path)
        vault._coordinator.prepare_index_read()
        row = vault._index_mgr.observations.rows()[canonical]
        assert row.presence == "removed"
        assert row.removed_observed_at is not None
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


@pytest.mark.parametrize("operation", ["write", "move"])
def test_failed_save_keeps_evidence_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    if operation == "move":
        (source / "a.md").write_text("hello")
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        if operation == "write":
            vault._doc_mgr.write("a.md", "server")
        else:
            vault._doc_mgr.rename("a.md", "b.md", update_links=False)
        ledger = vault._index_mgr.observations
        save = ledger._save

        def fail(_rows: object, _navigation: object = None) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(ledger, "_save", fail)
        with pytest.raises(OSError, match="disk full"):
            ledger.refresh_many({"a.md", "b.md"})
        monkeypatch.setattr(ledger, "_save", save)
        if operation == "move":
            ledger.sweep()
            rows = ledger.rows()
            assert rows["a.md"].moved_to == "b.md"
            assert rows["b.md"].moved_from == "a.md"
        else:
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
        assert json.loads(ledger_path.read_text())["version"] == 3
    finally:
        vault.close()


def test_reindex_after_restart_repairs_interrupted_write(tmp_path: Path) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("before")
    settings = VaultSettings(read_only=False, state_path=tmp_path / "state")
    vault = Vault(source_dir=source, settings=settings)
    try:
        vault.index.build_index()
        before = vault._index_mgr.observations.rows()["a.md"]
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault._doc_mgr.write("a.md", "after")
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


def test_folder_move_batches_ledger_save(
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

        def record(rows: object, navigation: dict[str, object] | None = None) -> None:
            calls.append(1)
            save(rows, navigation)  # type: ignore[arg-type]

        monkeypatch.setattr(ledger, "_save", record)
        vault._doc_mgr.move_folder("old", "new")
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
    sweep = writer = None
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
        if sweep is not None:
            sweep.join(timeout=5)
        if writer is not None:
            writer.join(timeout=5)
        vault.close()


def test_invalid_yaml_rename_queues_dirty_paths_and_records_move(
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

        def fail(_rows: object, _navigation: object = None) -> None:
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
            result = original(visit)
            entered.set()
            assert release.wait(5)
            return result

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
        assert before.presence == "present"
        assert after.presence == "removed"
        assert after.removed_observed_at is not None
    finally:
        release.set()
        if worker is not None:
            worker.join(timeout=5)
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
        assert rows["b.md"].moved_from is None
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


@pytest.mark.parametrize(
    "case", ["sweep_edit", "sweep_unlink", "sweep_rewrite", "dirty_edit"]
)
def test_write_hint_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    vault = Vault(source_dir=source, settings=VaultSettings(read_only=False))
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault._doc_mgr.write("a.md", "server")
        note = source / "a.md"
        ledger = vault._index_mgr.observations
        if case == "sweep_unlink":
            note.unlink()
        else:
            note.write_text("external")
        if case == "sweep_rewrite":
            original = ledger._inspector.observe_files

            def interleaved(visit):
                blocked = original(visit)
                vault._doc_mgr.write("a.md", "server")
                return blocked

            monkeypatch.setattr(ledger._inspector, "observe_files", interleaved)
        if case != "dirty_edit":
            ledger.sweep()
        if case in {"sweep_edit", "sweep_unlink"}:
            note.write_text("server")
        ledger.refresh_many({"a.md"})
        row = ledger.rows()["a.md"]
        assert row.origin == ("server_write" if case == "sweep_rewrite" else "unknown")
        if case == "dirty_edit":
            assert row.content_digest == hashlib.sha256(b"external").hexdigest()
    finally:
        vault.close()


@pytest.mark.parametrize("reconcile", ["refresh", "sweep"])
@pytest.mark.parametrize("drain_first", [False, True])
def test_recreated_source_preserves_proven_destination(
    tmp_path: Path, reconcile: str, drain_first: bool
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("first")
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault.writer.rename("a.md", "b.md", update_links=False)
        ledger = vault._index_mgr.observations
        if drain_first:
            ledger.refresh_many({"a.md", "b.md"})
        vault.writer.write("a.md", "second")
        if reconcile == "sweep":
            ledger.sweep()
        else:
            ledger.refresh_many({"a.md", "b.md"})
        rows = ledger.rows()
        assert rows["a.md"].presence == "present"
        assert rows["a.md"].moved_to is None
        assert rows["b.md"].moved_from == "a.md"
    finally:
        vault.close()


@pytest.mark.parametrize("reconcile", ["refresh", "sweep"])
@pytest.mark.parametrize("drain_first", [False, True])
@pytest.mark.parametrize("same_bytes", [False, True])
def test_recreated_source_moves_keep_each_proven_destination(
    tmp_path: Path, reconcile: str, drain_first: bool, same_bytes: bool
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("first")
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault.writer.rename("a.md", "b.md", update_links=False)
        ledger = vault._index_mgr.observations
        if drain_first:
            ledger.refresh_many({"a.md", "b.md"})
        vault.writer.write("a.md", "first" if same_bytes else "second")
        vault.writer.rename("a.md", "c.md", update_links=False)
        if reconcile == "sweep":
            ledger.sweep()
        else:
            ledger.refresh_many({"a.md", "b.md", "c.md"})
        rows = ledger.rows()
        assert rows["a.md"].moved_to == "c.md"
        assert rows["b.md"].moved_from == "a.md"
        assert rows["c.md"].moved_from == "a.md"
    finally:
        vault.close()


@pytest.mark.parametrize("same_bytes", [False, True])
@pytest.mark.parametrize("interruption", ["blocked", "save_failure"])
@pytest.mark.parametrize("replace_destination", [False, True])
def test_recreated_source_move_evidence_survives_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption: str,
    replace_destination: bool,
    same_bytes: bool,
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("first")
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault.writer.rename("a.md", "b.md", update_links=False)
        vault.writer.write("a.md", "first" if same_bytes else "second")
        vault.writer.rename("a.md", "c.md", update_links=False)
        if replace_destination:
            (source / "b.md").write_text("external")
        ledger = vault._index_mgr.observations
        inspector = ledger._inspector
        observe = inspector.observe_path
        save = ledger._save

        def blocked(path: str, visit: object) -> str:
            if path == "b.md":
                return "blocked"
            return observe(path, visit)  # type: ignore[arg-type]

        def fail(_rows: object, _navigation: object = None) -> None:
            raise OSError("disk full")

        if interruption == "blocked":
            monkeypatch.setattr(inspector, "observe_path", blocked)
            ledger.refresh_many({"a.md", "b.md", "c.md"})
            monkeypatch.setattr(inspector, "observe_path", observe)
        else:
            monkeypatch.setattr(ledger, "_save", fail)
            with pytest.raises(OSError, match="disk full"):
                ledger.refresh_many({"a.md", "b.md", "c.md"})
            monkeypatch.setattr(ledger, "_save", save)
        ledger.sweep()
        rows = ledger.rows()
        assert rows["a.md"].moved_to == "c.md"
        assert rows["c.md"].moved_from == "a.md"
        assert rows["b.md"].moved_from == (None if replace_destination else "a.md")
    finally:
        vault.close()


@pytest.mark.parametrize("recovery", ["refresh", "sweep"])
def test_blocked_older_move_does_not_claim_replaced_latest_incarnation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recovery: str
) -> None:
    source = tmp_path / "vault"
    source.mkdir()
    (source / "a.md").write_text("same")
    vault = Vault(
        source_dir=source,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        vault.index.build_index()
        vault._doc_mgr._mark_paths_dirty = lambda _paths: None
        vault.writer.rename("a.md", "b.md", update_links=False)
        vault.writer.write("a.md", "same")
        vault.writer.rename("a.md", "c.md", update_links=False)
        (source / "c.md").write_text("external")
        ledger = vault._index_mgr.observations
        inspector = ledger._inspector
        observe = inspector.observe_path

        def blocked(path: str, visit: object) -> str:
            if path == "b.md":
                return "blocked"
            return observe(path, visit)  # type: ignore[arg-type]

        monkeypatch.setattr(inspector, "observe_path", blocked)
        ledger.refresh_many({"a.md", "b.md", "c.md"})
        monkeypatch.setattr(inspector, "observe_path", observe)
        if recovery == "refresh":
            ledger.refresh_many({"a.md", "b.md"})
        else:
            ledger.sweep()
        rows = ledger.rows()
        assert rows["b.md"].moved_from == "a.md"
        assert rows["c.md"].moved_from is None
        assert rows["a.md"].presence == "removed"
        assert rows["a.md"].moved_to is None
    finally:
        vault.close()
