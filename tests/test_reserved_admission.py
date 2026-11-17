"""Admission of OKF navigation and history under a frontmatter gate."""

from pathlib import Path
from typing import Any

import pytest

from markdown_vault_mcp.vault import Vault, VaultSettings


def _fail_log_tombstone(manager: Any, patch: pytest.MonkeyPatch) -> None:
    original = manager._tombstone_skip
    original_stat = Path.stat

    def fail_log_stat(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.name == "log.md":
            raise OSError("transient stat")
        return original_stat(path, *args, **kwargs)

    def tombstone(skip: Any, *args: Any, **kwargs: Any) -> Any:
        if skip.path != "log.md":
            return original(skip, *args, **kwargs)
        with patch.context() as stat_patch:
            stat_patch.setattr(Path, "stat", fail_log_stat)
            return original(skip, *args, **kwargs)

    patch.setattr(manager, "_tombstone_skip", tombstone)


@pytest.mark.parametrize(
    ("mode", "expected"), [("on", True), ("off", False), ("auto", True)]
)
def test_reserved_admission_on_full_build(
    tmp_path: Path, mode: str, expected: bool
) -> None:
    (tmp_path / "index.md").write_text("---\nokf_version: '0.2'\n---\n# Index\n")
    (tmp_path / "log.md").write_text("# Log\n")
    (tmp_path / "concept.md").write_text("# Concept\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(okf_mode=mode, required_frontmatter=["title"]),
    )
    try:
        vault.index.build_index()
        paths = {note.path for note in vault.reader.list_documents()}
        assert ("index.md" in paths) is expected
        assert ("log.md" in paths) is expected
        assert "concept.md" not in paths
        assert (
            vault._tracker.skip_reasons()["concept.md"]["category"]
            == "missing_frontmatter"
        )
    finally:
        vault.close()


def test_generated_folder_index_survives_version_gate(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text("---\nokf_version: '0.2'\n---\n# Index\n")
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "index.md").write_text("# Folder\n")
    (tmp_path / "concept.md").write_text("# Concept\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, required_frontmatter=["okf_version"]),
    )
    try:
        vault.index.build_index()
        vault._index_mgr.process_dirty_paths({"folder/index.md", "concept.md"})
        paths = {n.path for n in vault.reader.list_documents()}
        assert "folder/index.md" in paths
        assert "concept.md" not in paths
    finally:
        vault.close()


def test_old_semantics_rebuilds_on_open(tmp_path: Path) -> None:
    import json
    import sqlite3

    from markdown_vault_mcp.hashing import compute_file_hash

    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text("# Index\n")
    (root / "log.md").write_text("# History\n")
    db = tmp_path / "index.db"
    settings = VaultSettings(
        index_path=db,
        state_path=tmp_path / "state.json",
        okf_mode="on",
        required_frontmatter=["title"],
    )
    vault = Vault(source_dir=root, settings=settings)
    try:
        # Recreate an old skipped row without changing the build settings.
        from markdown_vault_mcp.types import SkippedFile

        vault.index.build_index()
        vault._fts.upsert_tombstone(
            SkippedFile(
                path="index.md", category="missing_frontmatter", detail="missing title"
            ),
            content_hash="old",
            modified_at=(root / "index.md").stat().st_mtime,
        )
        vault._fts.delete_by_path("log.md")
        assert not vault.reader.list_documents()
    finally:
        vault.close()
    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text())
    state["indexed"].pop("log.md", None)
    state["skipped"]["log.md"] = compute_file_hash(root / "log.md")
    state["skip_reasons"].pop("log.md", None)
    state_path.write_text(json.dumps(state))
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE meta SET value = '12' WHERE key = 'index_semantics_version'"
        )
        conn.execute("DELETE FROM meta WHERE key = 'okf_admission_active'")
    vault = Vault(source_dir=root, settings=settings)
    try:
        assert not vault._coordinator._can_warm_start()
        vault.index.build_index()
        assert {n.path for n in vault.reader.list_documents()} == {"index.md", "log.md"}
        assert "log.md" not in vault._tracker._load_state()[1]
    finally:
        vault.close()

    _assert_ungated_old_semantics(tmp_path, root)


def _assert_ungated_old_semantics(tmp_path: Path, root: Path) -> None:
    import sqlite3

    ungated = VaultSettings(
        index_path=tmp_path / "ungated.db", state_path=tmp_path / "ungated.json"
    )
    vault = Vault(source_dir=root, settings=ungated)
    try:
        vault.index.build_index()
    finally:
        vault.close()
    with sqlite3.connect(tmp_path / "ungated.db") as conn:
        conn.execute(
            "UPDATE meta SET value = '12' WHERE key = 'index_semantics_version'"
        )
        conn.execute("DELETE FROM meta WHERE key = 'okf_admission_active'")
    vault = Vault(source_dir=root, settings=ungated)
    try:
        assert not vault._coordinator._can_warm_start()
    finally:
        vault.close()


def test_body_only_index_and_incremental_scan(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text("# Navigation\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(okf_mode="on", required_frontmatter=["title"]),
    )
    try:
        vault.index.build_index()
        assert {n.path for n in vault.reader.list_documents()} == {"index.md"}
        (tmp_path / "log.md").write_text("# History\n")
        (tmp_path / "concept.md").write_text("# Concept\n")
        vault.index.reindex()
        assert {n.path for n in vault.reader.list_documents()} == {"index.md", "log.md"}
        assert (
            vault._tracker.skip_reasons()["concept.md"]["category"]
            == "missing_frontmatter"
        )
    finally:
        vault.close()


@pytest.mark.parametrize("initial", ["inactive", "active", "invalid"])
@pytest.mark.parametrize("route", ["reindex", "write"])
def test_declaration_flip_refreshes_reserved_files(
    tmp_path: Path, initial: str, route: str
) -> None:
    active = "---\nokf_version: '0.2'\n---\n# Root\n"
    inactive = "# Root\n"
    start_active = initial == "active"
    initial_text = (
        "---\nokf_version: [\n---\n# Root\n"
        if initial == "invalid"
        else (active if start_active else inactive)
    )
    (tmp_path / "index.md").write_text(initial_text)
    (tmp_path / "log.md").write_text("# Log\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub/index.md").write_text("# Folder\n")
    (tmp_path / "concept.md").write_text("# Concept\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, required_frontmatter=["title"]),
    )
    try:
        vault.index.build_index()
        if route == "write":
            vault.writer.write(
                "index.md",
                "# Root\n",
                frontmatter=None if start_active else {"okf_version": "0.2"},
            )
            assert vault.index.wait_for_drain(timeout=5)
        else:
            (tmp_path / "index.md").write_text(inactive if start_active else active)
            vault.index.reindex()
        paths = {n.path for n in vault.reader.list_documents()}
        assert ("log.md" in paths) is not start_active
        assert ("sub/index.md" in paths) is not start_active
        assert "concept.md" not in paths
    finally:
        vault.close()


@pytest.mark.parametrize(("initial", "next_mode"), [("off", "on"), ("on", "off")])
def test_warm_mode_change_with_preconfigured_fields(
    tmp_path: Path, initial: str, next_mode: str
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text("# Index\n")
    (root / "log.md").write_text("# Log\n")

    def settings(mode: str) -> VaultSettings:
        return VaultSettings(
            index_path=tmp_path / "index.db",
            state_path=tmp_path / "state.json",
            required_frontmatter=["title"],
            indexed_frontmatter_fields=["type", "status", "stale_after"],
            okf_mode=mode,
        )

    vault = Vault(source_dir=root, settings=settings(initial))
    try:
        vault.index.build_index()
    finally:
        vault.close()
    vault = Vault(source_dir=root, settings=settings(next_mode))
    try:
        vault.index.build_index()
        assert ("log.md" in {n.path for n in vault.reader.list_documents()}) is (
            next_mode == "on"
        )
    finally:
        vault.close()


@pytest.mark.parametrize("mode", ["on", "off"])
def test_backend_without_admission_metadata_rebuilds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    (tmp_path / "index.md").write_text("# Root\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(okf_mode=mode, required_frontmatter=["title"]),
    )
    try:
        monkeypatch.delattr(type(vault._fts), "set_okf_admission_meta")
        monkeypatch.delattr(type(vault._fts), "get_okf_admission_meta")
        vault.index.build_index()
        assert not vault._coordinator._can_warm_start()
    finally:
        vault.close()


@pytest.mark.parametrize("start_active", [False, True])
def test_dirty_transition_then_reindex_keeps_tracker_consistent(
    tmp_path: Path, start_active: bool
) -> None:
    active = "---\nokf_version: '0.2'\n---\n# Root\n"
    inactive = "# Root\n"
    root = tmp_path / "index.md"
    root.write_text(active if start_active else inactive)
    (tmp_path / "log.md").write_text("# Log\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        vault.index.build_index()
        root.write_text(inactive if start_active else active)
        vault._index_mgr.process_dirty_paths({"index.md"})
        assert (
            "log.md" in {n.path for n in vault.reader.list_documents()}
        ) is not start_active
        assert vault._fts.get_okf_admission_meta() is None
        vault._index_mgr.process_dirty_paths({"index.md"})
        vault.index.reindex()
        assert (
            "log.md" in {n.path for n in vault.reader.list_documents()}
        ) is not start_active
        root.write_text(active if start_active else inactive)
        vault._index_mgr.process_dirty_paths({"index.md"})
        vault.index.reindex()
        assert (
            "log.md" in {n.path for n in vault.reader.list_documents()}
        ) is start_active
    finally:
        vault.close()


def test_reindex_transition_counts_each_note_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    from tests.conftest import MockEmbeddingProvider

    root = tmp_path / "index.md"
    root.write_text("# Root\n")
    (tmp_path / "log.md").write_text("# Log\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(required_frontmatter=["title"]),
        embedding_provider=MockEmbeddingProvider(),
    )
    try:
        vault.index.build_index()
        root.write_text("---\nokf_version: '0.2'\n---\n# Root\n")
        result = vault.index.reindex()
        assert (result.added, result.modified, result.unchanged, result.skipped) == (
            1,
            0,
            1,
            0,
        )
        root.write_text("# Root\n")
        result = vault.index.reindex()
        assert (result.added, result.modified, result.unchanged, result.skipped) == (
            0,
            0,
            0,
            2,
        )
        vault.index.build_embeddings()
        vectors = vault._index_mgr._embeddings._loaded_store()
        assert vectors is not None
        checkpoint = MagicMock(wraps=vectors.checkpoint)
        monkeypatch.setattr(vectors, "checkpoint", checkpoint)
        monkeypatch.setattr(vault._index_mgr, "_okf_admission_active", lambda: True)
        result = vault.index.reindex()
        assert (result.added, result.modified) == (0, 0)
        assert "log.md" in {n.path for n in vault.reader.list_documents()}
        checkpoint.assert_called()
    finally:
        vault.close()


def test_incomplete_walk_does_not_block_ordinary_reindex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.scanner as scanner
    import markdown_vault_mcp.tracker as tracker

    root = tmp_path / "index.md"
    root.write_text("---\nokf_version: '0.2'\n---\n# Root\n")
    note = tmp_path / "note.md"
    note.write_text("---\ntitle: A\n---\n# A\n")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "x.md").write_text("# X\n")
    original = scanner.iter_markdown_files

    def incomplete(*args: Any, **kwargs: Any) -> Any:
        callback = kwargs.get("on_error")
        if callback is not None:
            callback(PermissionError(13, "unreadable", str(blocked)))
        return original(*args, **kwargs)

    monkeypatch.setattr(scanner, "iter_markdown_files", incomplete)
    monkeypatch.setattr(tracker, "iter_markdown_files", incomplete)
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(okf_mode="auto", required_frontmatter=["title"]),
    )
    try:
        vault.index.build_index()
        note.write_text("---\ntitle: B\n---\n# B\n")
        result = vault.index.reindex()
        assert result.modified == 1
        assert result.walk_complete is False
        assert vault._fts.get_okf_admission_meta() is None
    finally:
        vault.close()


def test_scope_snapshot_stays_fixed_during_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.scanner as scanner

    root = tmp_path / "index.md"
    active = "---\nokf_version: '0.2'\n---\n# Root\n"
    root.write_text(active)
    (tmp_path / "a.md").write_text("# First\n")
    (tmp_path / "log.md").write_text("# Log\n")
    original = scanner.parse_note_categorized

    def parse_with_temporary_flip(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if kwargs.get("rel_path") == "a.md":
            root.write_text("# Root\n")
        return result

    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        monkeypatch.setattr(
            scanner, "parse_note_categorized", parse_with_temporary_flip
        )
        vault.index.build_index()
        root.write_text(active)
        assert "log.md" in {n.path for n in vault.reader.list_documents()}
        vault.index.reindex()
        assert "log.md" in {n.path for n in vault.reader.list_documents()}
    finally:
        vault.close()


@pytest.mark.parametrize("failure", ["parse", "tombstone"])
def test_failed_reserved_reconciliation_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    import markdown_vault_mcp.managers.index as index_module

    root = tmp_path / "index.md"
    active = "---\nokf_version: '0.2'\n---\n# Root\n"
    root.write_text("# Root\n" if failure == "parse" else active)
    (tmp_path / "log.md").write_text("# Log\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    original = index_module.parse_note_categorized

    def fail_log(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("rel_path") == "log.md":
            return None
        return original(*args, **kwargs)

    try:
        vault.index.build_index()
        root.write_text(active if failure == "parse" else "# Root\n")
        with monkeypatch.context() as patch:
            if failure == "parse":
                patch.setattr(index_module, "parse_note_categorized", fail_log)
            else:
                _fail_log_tombstone(vault._index_mgr, patch)
            vault.index.reindex()
        assert vault._fts.get_okf_admission_meta() is None
        if failure == "tombstone":
            assert "log.md" not in vault._tracker.skip_reasons()
        vault.index.reindex()
        assert ("log.md" in {n.path for n in vault.reader.list_documents()}) is (
            failure == "parse"
        )
        assert vault._fts.get_okf_admission_meta() is (failure == "parse")
    finally:
        vault.close()


def test_dirty_reserved_failure_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "index.md"
    root.write_text("# Root\n")
    (tmp_path / "log.md").write_text("# Log\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        vault.index.build_index()
        root.write_text("---\nokf_version: '0.2'\n---\n# Root\n")
        original = vault._index_mgr._process_dirty_path

        def fail_log(path: str, *, exempt: bool | None = None) -> None:
            if path == "log.md":
                raise OSError("transient")
            original(path, exempt=exempt)

        with monkeypatch.context() as patch:
            patch.setattr(vault._index_mgr, "_process_dirty_path", fail_log)
            with pytest.raises(OSError, match="transient"):
                vault._index_mgr.process_dirty_paths({"index.md"})
        assert vault._fts.get_okf_admission_meta() is None
        vault._index_mgr.process_dirty_paths({"index.md"})
        assert "log.md" in {n.path for n in vault.reader.list_documents()}
    finally:
        vault.close()


def test_no_required_fields_skips_admission_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "index.md").write_text("# Root\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        vault.index.build_index()
        assert vault._fts.get_okf_admission_meta() is None
        assert vault._coordinator._can_warm_start()
        monkeypatch.setattr(vault._index_mgr, "_okf_admission_active", None)
        vault.index.reindex()
        assert vault._fts.get_okf_admission_meta() is None
    finally:
        vault.close()


def test_dirty_flip_back_before_tracker_reconciliation(tmp_path: Path) -> None:
    root = tmp_path / "index.md"
    active = "---\nokf_version: '0.2'\n---\n# Root\n"
    root.write_text(active)
    (tmp_path / "log.md").write_text("# Log\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        vault.index.build_index()
        root.write_text("# Root\n")
        vault._index_mgr.process_dirty_paths({"index.md"})
        assert "log.md" not in {n.path for n in vault.reader.list_documents()}
        root.write_text(active)
        vault._index_mgr.process_dirty_paths({"index.md"})
        assert "log.md" in {n.path for n in vault.reader.list_documents()}
        vault.index.reindex()
        assert "log.md" in {n.path for n in vault.reader.list_documents()}
    finally:
        vault.close()


def test_dirty_transition_does_not_repeat_maintenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "index.md"
    root.write_text("# Root\n")
    (tmp_path / "log.md").write_text("# Log\n")
    (tmp_path / "other.md").write_text("# Other\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        vault.index.build_index()
        root.write_text("---\nokf_version: '0.2'\n---\n# Root\n")
        vault._index_mgr.process_dirty_paths({"index.md"})
        seen: list[str] = []
        original = vault._index_mgr._process_dirty_path

        def record(path: str, *, exempt: bool | None = None) -> None:
            seen.append(path)
            original(path, exempt=exempt)

        monkeypatch.setattr(vault._index_mgr, "_process_dirty_path", record)
        vault._index_mgr.process_dirty_paths({"other.md"})
        assert seen == ["other.md"]
        assert vault._fts.get_okf_admission_meta() is None
    finally:
        vault.close()


def test_build_failed_tombstone_retries_after_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "index.md"
    root.write_text("# Root\n")
    (tmp_path / "log.md").write_text("# Log\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        vault.index.build_index()
        assert vault._fts.get_okf_admission_meta() is False
        with monkeypatch.context() as patch:
            _fail_log_tombstone(vault._index_mgr, patch)
            vault.index.build_index(force=True)
        assert "log.md" not in vault._tracker.skip_reasons()
        assert vault._fts.get_okf_admission_meta() is None
        root.write_text("---\nokf_version: '0.2'\n---\n# Root\n")
        vault.index.reindex()
        assert "log.md" in {n.path for n in vault.reader.list_documents()}
    finally:
        vault.close()


def test_admission_metadata_roundtrip_and_dirty_reopen(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text("# Root\n")
    (root / "log.md").write_text("# Log\n")
    settings = VaultSettings(
        index_path=tmp_path / "index.db",
        state_path=tmp_path / "state.json",
        required_frontmatter=["title"],
    )
    vault = Vault(source_dir=root, settings=settings)
    try:
        for value in (True, False, None):
            vault._fts.set_okf_admission_meta(value)
            assert vault._fts.get_okf_admission_meta() is value
        vault._fts._conn().execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("okf_admission_active", "invalid"),
        )
        assert vault._fts.get_okf_admission_meta() is None
        vault.index.build_index()
        index.write_text("---\nokf_version: '0.2'\n---\n# Root\n")
        vault._index_mgr.process_dirty_paths({"index.md"})
        assert vault._fts.get_okf_admission_meta() is None
    finally:
        vault.close()
    vault = Vault(source_dir=root, settings=settings)
    try:
        assert not vault._coordinator._can_warm_start()
        vault.index.build_index()
        assert vault._coordinator._can_warm_start()
        assert "log.md" in {n.path for n in vault.reader.list_documents()}
    finally:
        vault.close()


@pytest.mark.parametrize("persistent", [False, True])
def test_build_transient_reserved_read_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, persistent: bool
) -> None:
    import markdown_vault_mcp.managers.index as index_module
    import markdown_vault_mcp.scanner as scanner

    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text("# Root\n")
    (root / "log.md").write_text("# Uniquehistorytoken\n")
    settings = VaultSettings(
        index_path=tmp_path / "index.db",
        state_path=tmp_path / "state.json",
        required_frontmatter=["title"],
        indexed_frontmatter_fields=["type", "status", "stale_after"],
    )
    vault = Vault(source_dir=root, settings=settings)
    original = scanner.parse_note_categorized
    calls: list[str] = []

    def unread_log(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("rel_path") == "log.md":
            calls.append("log.md")
            return None
        return original(*args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(scanner, "parse_note_categorized", unread_log)
            vault.index.build_index()
        assert "log.md" not in vault._tracker._load_state()[1]
        assert vault._fts.get_tombstone("log.md") is None
        assert vault._fts.get_okf_admission_meta() is False
        index.write_text("---\nokf_version: '0.2'\n---\n# Root\n")
        if persistent:
            with monkeypatch.context() as patch:
                patch.setattr(index_module, "parse_note_categorized", unread_log)
                vault.index.reindex()
                vault.index.reindex()
            assert len(calls) == 3
            assert "log.md" not in vault._tracker._load_state()[1]
        vault.index.reindex()
        assert "log.md" in {n.path for n in vault.reader.list_documents()}
        assert "log.md" in {n.path for n in vault.reader.search("Uniquehistorytoken")}
        again = vault.index.reindex()
        assert (again.added, again.modified) == (0, 0)
    finally:
        vault.close()
    vault = Vault(source_dir=root, settings=settings)
    try:
        assert vault._coordinator._can_warm_start()
    finally:
        vault.close()


@pytest.mark.parametrize("route", ["build", "reindex", "dirty", "reopen"])
@pytest.mark.parametrize("mode", ["auto", "on"])
def test_unreadable_okf_probe_preserves_reserved_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str, mode: str
) -> None:
    import markdown_vault_mcp.okf as okf
    from tests.conftest import MockEmbeddingProvider

    root = tmp_path / "vault"
    root.mkdir()
    index = root / "index.md"
    index.write_text("---\nokf_version: '0.2'\n---\n# Root\n")
    (root / "log.md").write_text("# Historytoken\n")
    (root / "folder").mkdir()
    (root / "folder/index.md").write_text("# Navigationtoken\n")
    (root / "concept.md").write_text("# Concept\n")
    settings = VaultSettings(
        index_path=tmp_path / "index.db",
        state_path=tmp_path / "state.json",
        embeddings_path=tmp_path / "vectors",
        required_frontmatter=["title"],
        indexed_frontmatter_fields=["type", "status", "stale_after"],
        okf_mode=mode,
    )
    provider = MockEmbeddingProvider()
    vault = Vault(source_dir=root, settings=settings, embedding_provider=provider)
    original = okf.read_text_utf8

    def fail_probe(path: Path, **kwargs: Any) -> str:
        if path == index:
            raise OSError("transient root probe")
        return original(path, **kwargs)

    try:
        vault.index.build_index()
        vault.index.build_embeddings()
        vectors = vault._index_mgr._embeddings._loaded_store()
        assert vectors is not None
        vector_count = vectors.count
        assert vector_count > 0 and vault._fts.get_okf_admission_meta() is True
        if route == "reopen":
            vault.close()
        with monkeypatch.context() as patch:
            patch.setattr(okf, "read_text_utf8", fail_probe)
            if route == "reopen":
                vault = Vault(
                    source_dir=root, settings=settings, embedding_provider=provider
                )
                assert vault._coordinator._can_warm_start()
                vault.index.build_index()
            elif route == "build":
                vault.index.build_index(force=True)
            elif route == "reindex":
                vault.index.reindex()
            else:
                vault._index_mgr.process_dirty_paths({"concept.md"})
            assert {n.path for n in vault.reader.list_documents()} == {
                "index.md",
                "log.md",
                "folder/index.md",
            }
            assert {
                n.path for n in vault.reader.search("Historytoken", mode="keyword")
            } == {"log.md"}
            assert vault._fts.get_okf_admission_meta() is True
            vectors = vault._search_mgr._load_vectors()
            assert vectors is not None
            assert vectors.count == vector_count
        vault.index.reindex()
        assert {
            n.path for n in vault.reader.search("Historytoken", mode="keyword")
        } == {"log.md"}
        assert vault._fts.get_okf_admission_meta() is True
    finally:
        vault.close()


def test_actual_root_declaration_removal_deactivates_okf(tmp_path: Path) -> None:
    root = tmp_path / "index.md"
    root.write_text("---\nokf_version: '0.2'\n---\n# Root\n")
    (tmp_path / "log.md").write_text("# Historytoken\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        vault.index.build_index()
        assert {
            n.path for n in vault.reader.search("Historytoken", mode="keyword")
        } == {"log.md"}
        root.unlink()
        vault.index.reindex()
        assert vault.reader.search("Historytoken", mode="keyword") == []
        assert vault.reader.list_documents() == []
        assert vault._fts.get_okf_admission_meta() is False
    finally:
        vault.close()


@pytest.mark.parametrize("confirmed_inactive", [False, True])
def test_unreadable_probe_without_active_fallback_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, confirmed_inactive: bool
) -> None:
    import markdown_vault_mcp.okf as okf

    root = tmp_path / "index.md"
    root.write_text("# Root\n")
    (tmp_path / "log.md").write_text("# Historytoken\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    original = okf.read_text_utf8

    def fail_probe(path: Path, **kwargs: Any) -> str:
        if path == root:
            raise OSError("transient root probe")
        return original(path, **kwargs)

    try:
        if confirmed_inactive:
            vault.index.build_index()
            assert vault._fts.get_okf_admission_meta() is False
        root.write_text("---\nokf_version: '0.2'\n---\n# Root\n")
        with monkeypatch.context() as patch:
            patch.setattr(okf, "read_text_utf8", fail_probe)
            if confirmed_inactive:
                vault.index.reindex()
            else:
                vault.index.build_index()
            assert vault._fts.get_okf_admission_meta() is (
                False if confirmed_inactive else None
            )
            assert vault.reader.search("Historytoken", mode="keyword") == []
        vault.index.reindex()
        assert {
            n.path for n in vault.reader.search("Historytoken", mode="keyword")
        } == {"log.md"}
        assert vault._fts.get_okf_admission_meta() is True
    finally:
        vault.close()
