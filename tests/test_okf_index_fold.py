"""Automatic index folding and designated folder boundaries."""

from pathlib import Path

import pytest

from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.utils.fs import path_exists
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_writer_drain
from tests.fixtures.designated_index import designated_vault


def test_folds_deep_notes_and_preserves_authored_boundary(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "projects/p/deep/more").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(root, (".", "projects/*"))
    try:
        vault.index.build_index()
        vault.writer.write("projects/p/deep/more/note.md", "# Deep note\n")
        wait_for_writer_drain(vault)
        assert "/projects/p/index.md" in (root / "index.md").read_text()
        assert path_exists(root / "projects/p/index.md")
        assert not path_exists(root / "projects/p/deep/index.md")
        assert not path_exists(root / "projects/p/deep/more/index.md")
        body = (root / "projects/p/index.md").read_text()
        assert "## deep/more/" in body
        assert "/projects/p/deep/more/note.md" in body
        vault.writer.delete("projects/p/deep/more/note.md")
        wait_for_writer_drain(vault)
        assert "note.md" not in (root / "projects/p/index.md").read_text()
    finally:
        vault.close()


def test_all_folders_retains_recursive_golden(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "a/b").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(root, ("**",))
    try:
        vault.index.build_index()
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


@pytest.mark.parametrize("operation", ["rename", "move", "move_designated"])
def test_structural_arrival_connects_new_project(
    tmp_path: Path, operation: str
) -> None:
    root = tmp_path / "vault"
    (root / "projects/p").mkdir(parents=True)
    (root / "source").mkdir()
    (root / "source/note.md").write_text("# Note\n")
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(root, ("projects/*",))
    try:
        vault.index.build_index()
        if operation == "rename":
            vault.writer.rename("source/note.md", "projects/p/note.md")
        elif operation == "move":
            vault.writer.move_folder("source", "projects/p/child")
        else:
            vault.writer.move_folder("source", "projects/q")
        wait_for_writer_drain(vault)
        if operation == "move_designated":
            assert "/projects/q/note.md" in (root / "projects/q/index.md").read_text()
            return
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
    vault = designated_vault(root, (".", "projects/*"))
    try:
        vault.index.build_index()
        vault.writer.write("projects/p/note.md", "# Note\n")
        wait_for_writer_drain(vault)
        assert (root / "projects/index.md").read_text() == authored
        assert (root / "index.md").read_text().endswith("# Root\n")
    finally:
        vault.close()


def test_oversized_indexes_refresh_preserving_headers_and_boundaries(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
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
        curated = (root / "index.md").read_bytes()
        navigation = vault.reader.okf_navigation("")
        assert (navigation.source, navigation.body, navigation.reason) == (
            None,
            None,
            "note_limit",
        )
        assert (root / "index.md").read_bytes() == curated
        vault.writer.write("deep/note.md", "# Note\n")
        wait_for_writer_drain(vault)
        contents = (root / "index.md").read_bytes()
        assert contents.startswith(header)
        assert b"/deep/index.md" in contents
        assert (root / "deep/index.md").read_bytes() == authored
        assert vault.okf.state().active
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
    vault = designated_vault(root, (".", "projects/*"))
    try:
        vault.index.build_index()
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
    vault = designated_vault(root, ("**",))
    try:
        vault.index.build_index()
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
    finally:
        vault.close()


def test_colliding_folder_headings_remain_individually_readable(tmp_path: Path) -> None:
    from markdown_it import MarkdownIt

    root = tmp_path / "vault"
    names = ("a b", "a  b", "a b (2)", "report # *bold*", "line\nbreak")
    for i, name in enumerate(names):
        (root / name).mkdir(parents=True)
        (root / name / f"note-{i}.md").write_text(f"# Note {i}\n")
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Index\n')
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        vault.writer.write("ordinary.md", "# Ordinary\n")
        wait_for_writer_drain(vault)
        headings = [
            entry.heading
            for entry in vault.reader.get_toc("index.md")
            if entry.level == 2
        ]
        assert len(headings) == len(set(headings)) == len(names)
        sections = [
            vault.reader.read("index.md", section=heading).content
            for heading in headings
        ]
        html = MarkdownIt().render((root / "index.md").read_text())
        assert "<h2>report # *bold*/</h2>" in html
        assert "<h2>line break/</h2>" in html
        for i in range(len(names)):
            assert sum(f"note-{i}.md" in section for section in sections) == 1
    finally:
        vault.close()


def test_explicit_regeneration_keeps_designated_fold(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "projects/p/deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(root, (".", "projects/*"))
    try:
        vault.index.build_index()
        vault.writer.write("projects/p/deep/first.md", "# First\n")
        wait_for_writer_drain(vault)
        vault.writer.okf_generate_index(folder="projects/p")
        assert not path_exists(root / "projects/p/deep/index.md")
        body = (root / "projects/p/index.md").read_text()
        assert "/projects/p/deep/first.md" in body
    finally:
        vault.close()


@pytest.mark.parametrize("target", ["absent", "alias"])
def test_explicit_generation_refuses_undesignated_or_aliased_target(
    tmp_path: Path, target: str
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError

    root = tmp_path / "vault"
    (root / "other").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    index = root / "other/index.md"
    if target == "alias":
        (root / "real.md").write_text("# Real\n")
        (root / "index.md").unlink()
        (root / "index.md").symlink_to(root / "real.md")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        with pytest.raises(InvalidRequestError, match="OKF_FOLDERS"):
            vault.writer.okf_generate_index(folder="" if target == "alias" else "other")
        if target == "absent":
            assert not path_exists(index)
            index.write_text("# Authored\n")
            with pytest.raises(InvalidRequestError, match="OKF_FOLDERS"):
                vault.writer.okf_generate_index(folder="other")
            assert index.read_text() == "# Authored\n"
        else:
            assert (root / "real.md").read_text() == "# Real\n"
    finally:
        vault.close()


def test_inactive_okf_keeps_recursive_onboarding_with_index_capability(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    (root / "child").mkdir(parents=True)
    (root / "child/note.md").write_text("# Note\n")
    caps = ContentConfig(
        okf_mode="auto", okf_index=True, okf_log="off", okf_folders=(".",)
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, okf_mode="auto", okf_capabilities=caps),
    )
    try:
        vault.index.build_index()
        assert not vault.okf.state().active
        vault.writer.okf_generate_index()
        assert "/child/index.md" in (root / "index.md").read_text()
        assert "/child/note.md" in (root / "child/index.md").read_text()
    finally:
        vault.close()


def test_explicit_creation_reconnects_parent(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "projects/p/deep").mkdir(parents=True)
    (root / "projects/p/deep/note.md").write_text("# Note\n")
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Index\n')
    first = designated_vault(root, (".",))
    try:
        first.index.build_index()
        first.writer.okf_generate_index()
        assert "/projects/p/deep/note.md" in (root / "index.md").read_text()
    finally:
        first.close()
    second = designated_vault(root, (".", "projects/*"))
    try:
        second.index.build_index()
        second.writer.okf_generate_index(folder="projects/p")
        assert "/projects/p/deep/note.md" in (root / "projects/p/index.md").read_text()
        body = (root / "index.md").read_text()
        assert "/projects/p/index.md" in body
        assert "/projects/p/deep/note.md" not in body
    finally:
        second.close()


def test_explicit_creation_stops_at_authored_boundary(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "docs/p").mkdir(parents=True)
    (root / "docs/p/note.md").write_text("# Note\n")
    root_body = '---\nokf_version: "0.2"\n---\n# Root\n'
    authored = "# Authored boundary\n"
    (root / "index.md").write_text(root_body)
    (root / "docs/index.md").write_text(authored)
    vault = designated_vault(root, (".", "docs/*"))
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="docs/p")
        assert "/docs/p/note.md" in (root / "docs/p/index.md").read_text()
        assert (root / "docs/index.md").read_text() == authored
        assert (root / "index.md").read_text() == root_body
    finally:
        vault.close()


def test_explicit_creation_reports_protected_parent_after_child_write(
    tmp_path: Path,
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError

    root = tmp_path / "vault"
    (root / "projects/p").mkdir(parents=True)
    (root / "projects/p/note.md").write_text("# Note\n")
    root_body = '---\nokf_version: "0.2"\n---\n# Root\n'
    (root / "index.md").write_text(root_body)
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".", "projects/*")
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=caps,
            protected_paths=["index.md"],
        ),
    )
    try:
        vault.index.build_index()
        with pytest.raises(InvalidRequestError) as exc:
            vault.writer.okf_generate_index(folder="projects/p")
        assert "projects/p/index.md" in str(exc.value)
        assert "did not write {'index.md':" in str(exc.value)
        assert "/projects/p/note.md" in (root / "projects/p/index.md").read_text()
        assert (root / "index.md").read_text() == root_body
    finally:
        vault.close()
    vault = designated_vault(root, (".", "projects/*"))
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="projects/p")
        assert "/projects/p/index.md" in (root / "index.md").read_text()
        vault.writer.write("projects/p/later.md", "# Later\n")
        wait_for_writer_drain(vault)
        assert "/projects/p/later.md" in (root / "projects/p/index.md").read_text()
        assert "/projects/p/index.md" in (root / "index.md").read_text()
    finally:
        vault.close()


def test_explicit_regeneration_of_existing_child_reconnects_parent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    (root / "projects/p").mkdir(parents=True)
    (root / "projects/p/note.md").write_text("# Note\n")
    (root / "projects/p/index.md").write_text("# Previous child\n")
    root_body = '---\nokf_version: "0.2"\n---\n# Parent\n'
    (root / "index.md").write_text(root_body)
    vault = designated_vault(root, (".", "projects/*"))
    try:
        vault.index.build_index()
        vault.writer.okf_generate_index(folder="projects/p")
        assert "/projects/p/note.md" in (root / "projects/p/index.md").read_text()
        assert "/projects/p/index.md" in (root / "index.md").read_text()
    finally:
        vault.close()
