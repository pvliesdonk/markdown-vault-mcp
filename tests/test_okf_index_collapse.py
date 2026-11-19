"""Collapse obsolete generated index boundaries."""

from pathlib import Path

import pytest

from markdown_vault_mcp.utils.fs import path_exists
from tests.fixtures.designated_index import designated_vault


def test_preview_classifies_boundaries(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    for folder in ("projects/p/deep", "projects/p/authored/child", "other"):
        (root / folder).mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "projects/p/index.md").write_text("# p\n")
    for folder in ("projects/p/deep", "projects/p/authored/child", "other"):
        name = folder.rsplit("/", 1)[-1]
        (root / folder / "note.md").write_text("# Note\n")
        (root / folder / "index.md").write_text(
            f"# {name}\n\n- [Note](/{folder}/note.md)\n"
        )
    (root / "projects/p/authored/index.md").write_text("# Authored\n")
    vault = designated_vault(root, ("projects/*",))
    try:
        vault.index.build_index()
        plan = vault.writer.okf_collapse_indexes()
        assert [path for path, _ in plan.removable] == ["projects/p/deep/index.md"]
        assert plan.parents == ("projects/p",)
        assert path_exists(root / "projects/p/deep/index.md")
        assert ("projects/p/authored/index.md", "authored") in plan.kept
        assert ("projects/p/authored/child/index.md", "below a kept index") in plan.kept
        assert ("other/index.md", "no designated parent") in plan.kept
    finally:
        vault.close()


@pytest.mark.parametrize("missing_parent", [False, True])
def test_apply_refreshes_designated_parent(
    tmp_path: Path, missing_parent: bool
) -> None:
    root = tmp_path / "vault"
    folder = "projects/p/deep" if missing_parent else "deep"
    parent = "projects/p" if missing_parent else ""
    (root / folder).mkdir(parents=True)
    root_text = '---\nokf_version: "0.2"\n---\n# Root\n'
    (root / "index.md").write_text(root_text)
    (root / folder / "index.md").write_text(f"# deep\n\n- [Note](/{folder}/note.md)\n")
    (root / folder / "note.md").write_text("# Note\n")
    vault = designated_vault(root, ("projects/*",) if missing_parent else (".",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_collapse_indexes(apply=True)
        assert [path for path, _ in result.removable] == [f"{folder}/index.md"]
        assert result.parents == (parent,)
        assert not path_exists(root / folder / "index.md")
        assert f"/{folder}/note.md" in (root / parent / "index.md").read_text()
        if missing_parent:
            assert (root / "index.md").read_text() == root_text
    finally:
        vault.close()


@pytest.mark.parametrize("changed", [True, False])
def test_apply_reports_failure_and_refreshes_parent(
    tmp_path: Path, changed: bool
) -> None:
    from markdown_vault_mcp._okf_index_collapse import apply_collapse, plan_collapse

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
        child = root / "b/index.md"
        if changed:
            child.write_text("# Changed\n")
        else:
            child.unlink()
        result = apply_collapse(vault._doc_mgr, vault._okf_migrate, plan)
        assert [path for path, _ in result.removable] == ["a/index.md"]
        reason = dict(result.kept)["b/index.md"]
        assert (
            reason == "changed since preview"
            if changed
            else reason.startswith("could not remove:")
        )
        body = (root / "index.md").read_text()
        assert "/a/note.md" in body
        assert ("/b/index.md" if changed else "/b/note.md") in body
    finally:
        vault.close()


@pytest.mark.parametrize("apply", [False, True])
def test_cli_preview_and_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, apply: bool
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
    args = ["okf", "collapse-indexes", "--source-dir", str(root)]
    result = CliRunner().invoke(app, [*args, "--apply"] if apply else args)
    assert result.exit_code == 0, result.output
    if apply:
        assert "Removed deep/index.md" in result.output
        assert "Regenerated ." in result.output
        assert not path_exists(child)
        assert path_exists(root / "index.md")
    else:
        assert "Would remove deep/index.md" in result.output
        assert "--apply" in result.output
        assert before == {
            p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*.md")
        }


@pytest.mark.parametrize("protected", ["deep/index.md", "index.md"])
def test_protected_paths_remain_unchanged(tmp_path: Path, protected: str) -> None:
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
            protected_paths=[protected],
        ),
    )
    try:
        vault.index.build_index()
        if protected == "index.md":
            with pytest.raises(InvalidRequestError, match="Cannot refresh"):
                vault.writer.okf_collapse_indexes(apply=True)
        else:
            preview = vault.writer.okf_collapse_indexes()
            applied = vault.writer.okf_collapse_indexes(apply=True)
            assert preview.removable == applied.removable == ()
            assert preview.kept == applied.kept
            assert preview.kept[0][0] == "deep/index.md"
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
