"""Collapse obsolete generated index boundaries."""

from pathlib import Path

import pytest

from markdown_vault_mcp.utils.fs import path_exists
from tests.fixtures.designated_index import designated_vault


def test_preview_keeps_root_and_finds_generated_child(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "projects/p/deep").mkdir(parents=True)
    root_index = '---\nokf_version: "0.2"\n---\n# Root\n'
    (root / "index.md").write_text(root_index)
    (root / "projects/p/index.md").write_text(
        "# p\n\n- [deep/](/projects/p/deep/index.md)\n"
    )
    child = root / "projects/p/deep/index.md"
    child.write_text("# deep\n\n- [Note](/projects/p/deep/note.md)\n")
    (root / "projects/p/deep/note.md").write_text("# Note\n")
    vault = designated_vault(root, ("projects/*",))
    try:
        vault.index.build_index()
        plan = vault.writer.okf_collapse_indexes()
        assert [path for path, _ in plan.removable] == ["projects/p/deep/index.md"]
        assert plan.parents == ("projects/p",)
        assert child.read_text().startswith("# deep")
        assert (root / "index.md").read_text() == root_index
    finally:
        vault.close()


def test_apply_refreshes_designated_root_and_preserves_frontmatter(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    header = '---\nokf_version: "0.2" # preserved\n---\n'
    (root / "index.md").write_text(header + "# Index\n\n- [deep/](/deep/index.md)\n")
    (root / "deep/index.md").write_text("# deep\n\n- [Note](/deep/note.md)\n")
    (root / "deep/note.md").write_text("# Note\n")
    (root / "log.md").write_text("# History\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_collapse_indexes(apply=True)
        assert [path for path, _ in result.removable] == ["deep/index.md"]
        assert not path_exists(root / "deep/index.md")
        assert (root / "index.md").read_text().startswith(header)
        assert "/deep/note.md" in (root / "index.md").read_text()
        assert (root / "log.md").read_text() == "# History\n"
    finally:
        vault.close()


def test_authored_boundary_blocks_descendants(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "a/b").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "a/index.md").write_text("# Authored\n")
    (root / "a/b/index.md").write_text("# b\n\n- [Note](/a/b/note.md)\n")
    (root / "a/b/note.md").write_text("# Note\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        plan = vault.writer.okf_collapse_indexes(apply=True)
        assert plan.removable == ()
        assert ("a/index.md", "authored") in plan.kept
        assert ("a/b/index.md", "below a kept index") in plan.kept
    finally:
        vault.close()


def test_missing_designated_parent_and_no_designated_parent(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "projects/p/deep").mkdir(parents=True)
    (root / "other").mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    for folder in ("projects/p/deep", "other"):
        name = folder.rsplit("/", 1)[-1]
        (root / folder / "note.md").write_text("# Note\n")
        (root / folder / "index.md").write_text(
            f"# {name}\n\n- [Note](/{folder}/note.md)\n"
        )
    vault = designated_vault(root, ("projects/*",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_collapse_indexes(apply=True)
        assert result.parents == ("projects/p",)
        assert ("other/index.md", "no designated parent") in result.kept
        assert "/projects/p/deep/note.md" in (root / "projects/p/index.md").read_text()
        assert (root / "index.md").read_text().endswith("# Root\n")
    finally:
        vault.close()


def test_changed_index_is_kept(tmp_path: Path) -> None:
    from markdown_vault_mcp._okf_index_collapse import apply_collapse, plan_collapse

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    child = root / "deep/index.md"
    child.write_text("# deep\n\n- [Note](/deep/note.md)\n")
    (root / "deep/note.md").write_text("# Note\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        plan = plan_collapse(vault._doc_mgr, vault._okf_migrate)
        child.write_text("# Changed\n")
        result = apply_collapse(vault._doc_mgr, vault._okf_migrate, plan)
        assert ("deep/index.md", "changed since preview") in result.kept
        assert child.read_text() == "# Changed\n"
    finally:
        vault.close()


def test_cli_preview_is_write_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    child = root / "deep/index.md"
    child.write_text("# deep\n\n- [Note](/deep/note.md)\n")
    (root / "deep/note.md").write_text("# Note\n")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*.md")}
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_INDEX", "true")
    result = CliRunner().invoke(
        app, ["okf", "collapse-indexes", "--source-dir", str(root)]
    )
    assert result.exit_code == 0, result.output
    assert "Would remove deep/index.md" in result.output
    assert "--apply" in result.output
    assert before == {
        p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*.md")
    }


def test_cli_apply_reports_removed_and_regenerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "deep/index.md").write_text("# deep\n\n- [Note](/deep/note.md)\n")
    (root / "deep/note.md").write_text("# Note\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_INDEX", "true")
    result = CliRunner().invoke(
        app, ["okf", "collapse-indexes", "--source-dir", str(root), "--apply"]
    )
    assert result.exit_code == 0, result.output
    assert "Removed deep/index.md" in result.output
    assert "Regenerated ." in result.output
    assert "/deep/note.md" in (root / "index.md").read_text()


def test_delete_failure_still_refreshes_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp._okf_index_collapse import apply_collapse, plan_collapse
    from markdown_vault_mcp.exceptions import InvalidRequestError

    root = tmp_path / "vault"
    for folder in ("a", "b"):
        (root / folder).mkdir(parents=True)
        (root / folder / "index.md").write_text(
            f"# {folder}\n\n- [Note](/{folder}/note.md)\n"
        )
        (root / folder / "note.md").write_text("# Note\n")
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        plan = plan_collapse(vault._doc_mgr, vault._okf_migrate)
        delete = vault._doc_mgr.delete

        def refuse(
            path: str, if_match: str | None = None, *, allow_protected: bool = False
        ) -> object:
            if path == "b/index.md":
                raise InvalidRequestError("refused")
            return delete(path, if_match=if_match, allow_protected=allow_protected)

        monkeypatch.setattr(vault._doc_mgr, "delete", refuse)
        result = apply_collapse(vault._doc_mgr, vault._okf_migrate, plan)
        assert [path for path, _ in result.removable] == ["a/index.md"]
        assert ("b/index.md", "could not remove: refused") in result.kept
        body = (root / "index.md").read_text()
        assert "/a/note.md" in body
        assert "/b/index.md" in body
    finally:
        vault.close()


def test_protected_candidate_stays_in_preview_and_apply(tmp_path: Path) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "deep/index.md").write_text("# deep\n\n- [Note](/deep/note.md)\n")
    (root / "deep/note.md").write_text("# Note\n")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".",)
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=caps,
            protected_paths=["deep/index.md"],
        ),
    )
    try:
        vault.index.build_index()
        preview = vault.writer.okf_collapse_indexes()
        applied = vault.writer.okf_collapse_indexes(apply=True)
        assert preview.removable == applied.removable == ()
        assert preview.kept == applied.kept
        assert preview.kept[0][0] == "deep/index.md"
    finally:
        vault.close()


def test_protected_parent_refuses_before_any_deletion(tmp_path: Path) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.vault import Vault, VaultSettings

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    child = root / "deep/index.md"
    child.write_text("# deep\n\n- [Note](/deep/note.md)\n")
    (root / "deep/note.md").write_text("# Note\n")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".",)
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
        with pytest.raises(InvalidRequestError, match="Cannot refresh"):
            vault.writer.okf_collapse_indexes(apply=True)
        assert child.read_text().startswith("# deep")
    finally:
        vault.close()


def test_cli_apply_respects_preview_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    child = root / "deep/index.md"
    child.write_text("# deep\n\n- [Note](/deep/note.md)\n")
    (root / "deep/note.md").write_text("# Note\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_INDEX", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROTECTED_PATHS", "deep/index.md")
    args = ["okf", "collapse-indexes", "--source-dir", str(root)]
    preview = CliRunner().invoke(app, args)
    applied = CliRunner().invoke(app, [*args, "--apply"])
    assert preview.exit_code == applied.exit_code == 0
    assert "Keep deep/index.md:" in preview.output
    assert "Keep deep/index.md:" in applied.output
    assert child.read_text().startswith("# deep")
