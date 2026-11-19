"""Automatic index folding and designated folder boundaries."""

from pathlib import Path

import pytest

from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.utils.fs import path_exists
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_writer_drain


def _vault(root: Path, folders: tuple[str, ...]) -> Vault:
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_log="off", okf_folders=folders
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="on", okf_capabilities=caps),
    )
    vault.index.build_index()
    return vault


def test_folds_deep_notes_and_preserves_authored_boundary(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "projects/p/deep/more").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = _vault(root, (".", "projects/*"))
    try:
        vault.writer.write("projects/p/deep/more/note.md", "# Deep note\n")
        wait_for_writer_drain(vault)
        assert "/projects/p/index.md" in (root / "index.md").read_text()
        assert path_exists(root / "projects/p/index.md")
        assert not path_exists(root / "projects/p/deep/index.md")
        assert not path_exists(root / "projects/p/deep/more/index.md")
        vault.writer.delete("projects/p/deep/more/note.md")
        wait_for_writer_drain(vault)
        assert "note.md" not in (root / "projects/p/index.md").read_text()
    finally:
        vault.close()


def test_all_folders_retains_recursive_golden(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "a/b").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = _vault(root, ("**",))
    try:
        vault.writer.write("a/b/note.md", "# Note\n")
        wait_for_writer_drain(vault)
        assert (
            (root / "index.md").read_text()
            == '---\nokf_version: "0.2"\n---\n# Index\n\n- [a/](/a/index.md)\n'
        )
        assert (root / "a/index.md").read_text() == "# a\n\n- [b/](/a/b/index.md)\n"
        assert (root / "a/b/index.md").read_text() == "# b\n\n- [Note](/a/b/note.md)\n"
    finally:
        vault.close()


@pytest.mark.parametrize("operation", ["rename", "move"])
def test_structural_arrival_connects_new_project(
    tmp_path: Path, operation: str
) -> None:
    root = tmp_path / "vault"
    (root / "projects/p").mkdir(parents=True)
    (root / "source").mkdir()
    (root / "source/note.md").write_text("# Note\n")
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = _vault(root, (".", "projects/*"))
    try:
        if operation == "rename":
            vault.writer.rename("source/note.md", "projects/p/note.md")
        else:
            vault.writer.move_folder("source", "projects/p/child")
        wait_for_writer_drain(vault)
        assert "/projects/p/index.md" in (root / "index.md").read_text()
        expected = (
            "/projects/p/note.md"
            if operation == "rename"
            else "/projects/p/child/note.md"
        )
        assert expected in (root / "projects/p/index.md").read_text()
    finally:
        vault.close()


def test_new_boundary_stops_at_authored_parent(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "projects/p").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    authored = "# Handwritten\n"
    (root / "projects/index.md").write_text(authored)
    vault = _vault(root, (".", "projects/*"))
    try:
        vault.writer.write("projects/p/note.md", "# Note\n")
        wait_for_writer_drain(vault)
        assert (root / "projects/index.md").read_text() == authored
        assert (root / "index.md").read_text().endswith("# Root\n")
    finally:
        vault.close()


def test_oversized_indexes_refresh_without_weakening_public_read(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from markdown_vault_mcp.exceptions import NoteTooLargeError

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    header = b'---\r\nokf_version: "0.2" # declaration\r\n---\r\n'
    (root / "index.md").write_bytes(header + b"# Root\n\n" + b"old content\n" * 30)
    authored = b"# Authored\n" + b"unchanged\n" * 30
    (root / "deep/index.md").write_bytes(authored)
    caps = ContentConfig(
        okf_mode="auto", okf_index=True, okf_log="off", okf_folders=(".",)
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            okf_mode="auto",
            okf_capabilities=caps,
            max_note_read_bytes=128,
        ),
    )
    try:
        vault.index.build_index()
        vault.writer.write("deep/note.md", "# Note\n")
        wait_for_writer_drain(vault)
        contents = (root / "index.md").read_bytes()
        assert contents.startswith(header)
        assert b"/deep/index.md" in contents
        assert (root / "deep/index.md").read_bytes() == authored
        assert vault.okf.state().active
        with pytest.raises(NoteTooLargeError):
            vault.reader.read("deep/index.md")
        # A second folded listing can grow past the limit while section reads work.
        for i in range(12):
            vault.writer.write(f"note-{i:02}.md", f"# Long title {i}\n")
        wait_for_writer_drain(vault)
        with pytest.raises(NoteTooLargeError):
            vault.reader.read("index.md")
        assert vault.reader.read("index.md", section="Index") is not None
        refreshed = (root / "index.md").read_bytes()
        assert refreshed.startswith(header)
        assert b"/note-11.md" in refreshed
        assert not any(
            record.msg.startswith("okf_convention_index_failed")
            for record in caplog.records
        )
    finally:
        vault.close()


def test_ordinary_write_repairs_new_and_removed_boundary(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "projects/p/deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = _vault(root, (".", "projects/*"))
    try:
        vault.writer.write("projects/p/deep/a.md", "# A\n")
        wait_for_writer_drain(vault)
        index = root / "projects/p/index.md"
        before = index.read_bytes()
        assert b"## deep" in before
        authored = b"# Handwritten index\n"
        vault.writer.write("projects/p/deep/index.md", authored.decode())
        assert index.read_bytes() == before
        vault.writer.write("projects/p/deep/c.md", "# C\n")
        wait_for_writer_drain(vault)
        assert b"/projects/p/deep/index.md" in index.read_bytes()
        assert b"## deep" not in index.read_bytes()
        assert (root / "projects/p/deep/index.md").read_bytes() == authored
        vault.writer.delete("projects/p/deep/index.md")
        vault.writer.write("projects/p/deep/d.md", "# D\n")
        wait_for_writer_drain(vault)
        assert b"## deep" in index.read_bytes()
        assert b"/projects/p/deep/index.md" not in index.read_bytes()
    finally:
        vault.close()


@pytest.mark.parametrize("automatic", [True, False])
def test_generation_queries_subtree_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, automatic: bool
) -> None:
    root = tmp_path / "vault"
    for i in range(30):
        folder = root / f"folder-{i:02}" / "nested"
        folder.mkdir(parents=True)
        (folder / "note.md").write_text("# Note\n")
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = _vault(root, ("**",))
    try:
        original = vault._search_mgr.list
        calls: list[str | None] = []

        def counted(*, folder: str | None = None, **kwargs: object) -> object:
            calls.append(folder)
            return original(folder=folder, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(vault._search_mgr, "list", counted)
        if automatic:
            vault.writer.write("root-note.md", "# Root note\n")
        else:
            vault.writer.okf_generate_index()
        wait_for_writer_drain(vault)
        assert calls == [None]
        assert "/folder-00/index.md" in (root / "index.md").read_text()
        assert "/folder-00/nested/index.md" in (root / "folder-00/index.md").read_text()
    finally:
        vault.close()
