"""Admission of OKF navigation and history under a frontmatter gate."""

from pathlib import Path

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


def test_old_semantics_rebuilds_on_open(tmp_path: Path) -> None:
    import sqlite3

    from markdown_vault_mcp.fts_index import INDEX_SEMANTICS_VERSION

    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text("# Index\n")
    db = tmp_path / "index.db"
    settings = VaultSettings(
        index_path=db,
        state_path=tmp_path / "state.json",
        okf_mode="off",
        required_frontmatter=["title"],
    )
    vault = Vault(source_dir=root, settings=settings)
    try:
        vault.index.build_index()
    finally:
        vault.close()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE meta SET value = ? WHERE key = 'index_semantics_version'",
            (str(INDEX_SEMANTICS_VERSION - 1),),
        )
    settings = VaultSettings(
        index_path=db,
        state_path=tmp_path / "state.json",
        okf_mode="on",
        required_frontmatter=["title"],
    )
    vault = Vault(source_dir=root, settings=settings)
    try:
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
