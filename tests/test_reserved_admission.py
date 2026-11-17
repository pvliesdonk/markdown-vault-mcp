"""Admission of OKF navigation and history under a frontmatter gate."""

from pathlib import Path
from typing import Any

import pytest

from markdown_vault_mcp.vault import Vault, VaultSettings


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


def test_dirty_refresh_uses_live_detection(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text("# No declaration\n")
    (tmp_path / "log.md").write_text("# Log\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        vault.index.build_index()
        assert not vault.reader.list_documents()
        (tmp_path / "index.md").write_text("---\nokf_version: '0.2'\n---\n# Index\n")
        vault._index_mgr.process_dirty_paths({"index.md", "log.md"})
        assert {n.path for n in vault.reader.list_documents()} == {"index.md", "log.md"}
        (tmp_path / "index.md").write_text("# No declaration\n")
        vault._index_mgr.process_dirty_paths({"index.md", "log.md"})
        assert not vault.reader.list_documents()
    finally:
        vault.close()


def test_generated_folder_index_survives_version_gate(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text("---\nokf_version: '0.2'\n---\n# Index\n")
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "note.md").write_text(
        "---\nokf_version: '0.2'\n---\n# Note\n"
    )
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(read_only=False, required_frontmatter=["okf_version"]),
    )
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="folder")
        assert vault.index.wait_for_drain(timeout=5)
        assert "folder/index.md" in {n.path for n in vault.reader.list_documents()}
        # Simulate a conformant generated index without the legacy placeholder.
        (tmp_path / "folder" / "index.md").write_text("# Folder\n")
        vault._index_mgr.process_dirty_paths({"folder/index.md"})
        assert "folder/index.md" in {n.path for n in vault.reader.list_documents()}
    finally:
        vault.close()


def test_old_semantics_rebuilds_on_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    import markdown_vault_mcp.indexing.coordinator as coordinator
    from markdown_vault_mcp.fts_index import INDEX_SEMANTICS_VERSION

    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text("# Index\n")
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
        assert not vault.reader.list_documents()
    finally:
        vault.close()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE meta SET value = ? WHERE key = 'index_semantics_version'",
            (str(INDEX_SEMANTICS_VERSION - 1),),
        )
    vault = Vault(source_dir=root, settings=settings)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                coordinator, "INDEX_SEMANTICS_VERSION", INDEX_SEMANTICS_VERSION - 1
            )
            assert vault._coordinator._can_warm_start()
            vault.index.build_index()
            assert not vault.reader.list_documents()
    finally:
        vault.close()
    vault = Vault(source_dir=root, settings=settings)
    try:
        assert not vault._coordinator._can_warm_start()
        vault.index.build_index()
        assert "index.md" in {n.path for n in vault.reader.list_documents()}
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


@pytest.mark.parametrize("start_active", [False, True])
@pytest.mark.parametrize("route", ["reindex", "write"])
def test_declaration_flip_refreshes_reserved_files(
    tmp_path: Path, start_active: bool, route: str
) -> None:
    active = "---\nokf_version: '0.2'\n---\n# Root\n"
    inactive = "# Root\n"
    (tmp_path / "index.md").write_text(active if start_active else inactive)
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


def test_corrected_invalid_declaration_admits_reserved_files(tmp_path: Path) -> None:
    (tmp_path / "index.md").write_text("---\nokf_version: [\n---\n# Root\n")
    (tmp_path / "log.md").write_text("# Log\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        vault.index.build_index()
        assert not vault.reader.list_documents()
        (tmp_path / "index.md").write_text("---\nokf_version: '0.2'\n---\n# Root\n")
        vault.index.reindex()
        assert {n.path for n in vault.reader.list_documents()} == {"index.md", "log.md"}
    finally:
        vault.close()


def test_failed_reserved_reconciliation_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.managers.index as index_module

    (tmp_path / "index.md").write_text("# Root\n")
    (tmp_path / "log.md").write_text("# Log\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        vault.index.build_index()
        (tmp_path / "index.md").write_text("---\nokf_version: '0.2'\n---\n# Root\n")
        original = index_module.parse_note_categorized

        def fail_log(*args: Any, **kwargs: Any) -> Any:
            if kwargs.get("rel_path") == "log.md":
                return None
            return original(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(index_module, "parse_note_categorized", fail_log)
            vault.index.reindex()
        assert vault._fts.get_okf_admission_meta() is None
        vault.index.reindex()
        assert "log.md" in {n.path for n in vault.reader.list_documents()}
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
        monkeypatch.setattr(vault._fts, "set_okf_admission_meta", None)
        monkeypatch.setattr(vault._fts, "get_okf_admission_meta", None)
        vault.index.build_index()
        assert not vault._coordinator._can_warm_start()
        assert ("index.md" in {n.path for n in vault.reader.list_documents()}) is (
            mode == "on"
        )
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


def test_reindex_transition_counts_each_note_once(tmp_path: Path) -> None:
    root = tmp_path / "index.md"
    root.write_text("# Root\n")
    (tmp_path / "log.md").write_text("# Log\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
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
    finally:
        vault.close()


@pytest.mark.parametrize("mode", ["off", "auto"])
def test_incomplete_walk_does_not_block_ordinary_reindex(
    tmp_path: Path, mode: str
) -> None:
    root = tmp_path / "index.md"
    root.write_text("---\nokf_version: '0.2'\n---\n# Root\n")
    note = tmp_path / "note.md"
    note.write_text("---\ntitle: A\n---\n# A\n")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "x.md").write_text("# X\n")
    blocked.chmod(0)
    try:
        vault = Vault(
            source_dir=tmp_path,
            settings=VaultSettings(okf_mode=mode, required_frontmatter=["title"]),
        )
        try:
            vault.index.build_index()
            note.write_text("---\ntitle: B\n---\n# B\n")
            assert vault.index.reindex().modified == 1
        finally:
            vault.close()
    finally:
        blocked.chmod(0o700)


def test_scope_snapshot_stays_fixed_during_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import markdown_vault_mcp.scanner as scanner

    root = tmp_path / "index.md"
    active = "---\nokf_version: '0.2'\n---\n# Root\n"
    root.write_text(active)
    (tmp_path / "log.md").write_text("# Log\n")
    original = scanner.parse_note_categorized

    def parse_with_temporary_flip(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("rel_path") != "log.md":
            return original(*args, **kwargs)
        root.write_text("# Root\n")
        try:
            return original(*args, **kwargs)
        finally:
            root.write_text(active)

    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        monkeypatch.setattr(
            scanner, "parse_note_categorized", parse_with_temporary_flip
        )
        vault.index.build_index()
        assert "log.md" in {n.path for n in vault.reader.list_documents()}
        vault.index.reindex()
        assert "log.md" in {n.path for n in vault.reader.list_documents()}
    finally:
        vault.close()


def test_failed_tombstone_does_not_publish_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import inspect

    root = tmp_path / "index.md"
    root.write_text("---\nokf_version: '0.2'\n---\n# Root\n")
    (tmp_path / "log.md").write_text("# Log\n")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad/index.md").write_text("---\nbroken: [\n---\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        vault.index.build_index()
        root.write_text("# Root\n")
        original = Path.stat

        def fail_log_stat(self: Path, *args: Any, **kwargs: Any) -> Any:
            if self.name == "log.md" and any(
                f.function == "_tombstone_skip" for f in inspect.stack()
            ):
                raise OSError("transient stat")
            return original(self, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "stat", fail_log_stat)
            vault.index.reindex()
        assert "log.md" in {n.path for n in vault.reader.list_documents()}
        assert vault._fts.get_okf_admission_meta() is None
        vault.index.reindex()
        assert "log.md" not in {n.path for n in vault.reader.list_documents()}
        assert vault._fts.get_okf_admission_meta() is False
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
        monkeypatch.setattr(vault._fts, "get_okf_admission_meta", None)
        assert vault._coordinator._can_warm_start()
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
    import inspect

    root = tmp_path / "index.md"
    root.write_text("# Root\n")
    (tmp_path / "log.md").write_text("# Log\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    original = Path.stat

    def fail_log_stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == "log.md" and any(
            f.function == "_tombstone_skip" for f in inspect.stack()
        ):
            raise OSError("transient")
        return original(self, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(Path, "stat", fail_log_stat)
            vault.index.build_index()
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
