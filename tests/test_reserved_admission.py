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
        # Recreate old admission with the same current settings and provenance.
        vault._index_mgr._exempt_required_frontmatter = lambda _: False
        vault.index.build_index()
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
            with pytest.raises(OSError, match=r"log\.md"):
                vault.index.reindex()
        assert vault._fts.get_okf_admission_meta() is None
        vault.index.reindex()
        assert "log.md" in {n.path for n in vault.reader.list_documents()}
    finally:
        vault.close()


def test_backend_without_admission_metadata_rebuilds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "index.md").write_text("# Root\n")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(okf_mode="on", required_frontmatter=["title"]),
    )
    try:
        monkeypatch.setattr(vault._fts, "set_okf_admission_meta", None)
        monkeypatch.setattr(vault._fts, "get_okf_admission_meta", None)
        vault.index.build_index()
        assert not vault._coordinator._can_warm_start()
        assert "index.md" in {n.path for n in vault.reader.list_documents()}
    finally:
        vault.close()
