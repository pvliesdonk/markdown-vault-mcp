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
        assert [path for path, _, _ in plan.removable] == ["projects/p/deep/index.md"]
        assert "projects/p/index.md" in plan.refresh
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
    header = '---\nokf_version: "0.3"\ntitle: Bundle\ndescription: Test bundle\nlicense: CC-BY-4.0\n---\n'
    root_text = header + "# Root\n"
    (root / "index.md").write_text(root_text)
    (root / folder / "index.md").write_text(f"# deep\n\n- [Note](/{folder}/note.md)\n")
    (root / folder / "note.md").write_text("# Note\n")
    vault = designated_vault(root, ("projects/*",) if missing_parent else (".",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_collapse_indexes(apply=True)
        assert [path for path, _, _ in result.removable] == [f"{folder}/index.md"]
        assert (f"{parent}/index.md" if parent else "index.md") in result.refresh
        assert not path_exists(root / folder / "index.md")
        assert f"/{folder}/note.md" in (root / parent / "index.md").read_text()
        if missing_parent:
            assert (root / "index.md").read_text() == root_text
        else:
            assert (root / "index.md").read_text().startswith(header)
    finally:
        vault.close()


@pytest.mark.parametrize("changed", [True, False])
def test_apply_reports_failure_and_refreshes_parent(
    tmp_path: Path, changed: bool
) -> None:
    from markdown_vault_mcp._okf_index_collapse import (
        CollapseInterrupted,
        apply_collapse,
        plan_collapse,
    )

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
        if changed:
            result = apply_collapse(vault._doc_mgr, vault._okf_migrate, plan)
            assert [path for path, _, _ in result.removable] == ["a/index.md"]
            assert dict(result.kept)["b/index.md"] == "changed since preview"
        else:
            with pytest.raises(CollapseInterrupted) as interrupted:
                apply_collapse(vault._doc_mgr, vault._okf_migrate, plan)
            assert interrupted.value.removed == ("a/index.md",)
            assert "b/index.md" in interrupted.value.pending
        body = (root / "index.md").read_text()
        assert "/a/note.md" in body
        assert ("/b/index.md" if changed else "/b/note.md") in body
    finally:
        vault.close()


@pytest.mark.parametrize("refresh_fails", [False, True])
def test_post_unlink_failure_reports_uncertain_and_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refresh_fails: bool
) -> None:
    from markdown_vault_mcp._okf_index_collapse import (
        CollapseInterrupted,
        apply_collapse,
        plan_collapse,
    )

    root = tmp_path / "vault"
    for folder in ("a", "b", "c"):
        (root / folder).mkdir(parents=True)
        (root / folder / "index.md").write_text(
            f"# {folder}\n\n- [Note](/{folder}/note.md)\n"
        )
        (root / folder / "note.md").write_text("# Note\n")
    (root / "index.md").write_text("# Root\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        plan = plan_collapse(vault._doc_mgr, vault._okf_migrate)
        fire = vault._doc_mgr._notifier.fire

        def fail_after_unlink(path: Path, content: str, operation: str) -> None:
            if operation == "delete" and path.parent.name == "b":
                raise OSError("callback failed after unlink")
            fire(path, content, operation)

        monkeypatch.setattr(vault._doc_mgr._notifier, "fire", fail_after_unlink)
        generate = vault._okf_migrate.generate_index
        calls = 0

        def generate_with_failure(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            if refresh_fails and calls == 2:
                raise OSError("refresh failed")
            return generate(*args, **kwargs)

        if refresh_fails:
            monkeypatch.setattr(
                vault._okf_migrate, "generate_index", generate_with_failure
            )
        with pytest.raises(CollapseInterrupted) as interrupted:
            apply_collapse(vault._doc_mgr, vault._okf_migrate, plan)
        assert interrupted.value.removed == ("a/index.md",)
        assert "b/index.md" in interrupted.value.pending
        assert "c/index.md" in interrupted.value.pending
        assert isinstance(interrupted.value.__cause__, OSError)
        assert "callback failed after unlink" in str(interrupted.value.__cause__)
        assert not path_exists(root / "b/index.md")
        assert path_exists(root / "c/index.md")
        if refresh_fails:
            assert str(interrupted.value.__cause__.__cause__) == "refresh failed"
        else:
            body = (root / "index.md").read_text()
            assert "/a/note.md" in body
            assert "/b/note.md" in body
            assert "/c/index.md" in body
    finally:
        vault.close()


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("explicit_source", [False, True])
def test_cli_preview_and_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, apply: bool, explicit_source: bool
) -> None:
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    child = root / "deep/index.md"
    child.write_text("---\nowner: author\n---\n# deep\n\n- [Note](/deep/note.md)\n")
    (root / "deep/note.md").write_text("# Note\n")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*.md")}
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_INDEX", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    args = ["okf", "collapse-indexes"]
    if explicit_source:
        args += ["--source-dir", str(root)]
    result = CliRunner().invoke(app, [*args, "--apply"] if apply else args)
    assert result.exit_code == 0, result.output
    assert "deep/index.md (any frontmatter in it is removed too)" in result.output
    assert "may break links" in result.output
    assert "markdown-vault-mcp links broken" in result.output
    if apply:
        assert "Removed deep/index.md" in result.output
        assert "Regenerated index.md" in result.output
        assert not path_exists(child)
        assert path_exists(root / "index.md")
    else:
        assert "Would remove deep/index.md" in result.output
        assert "Would regenerate index.md" in result.output
        assert "--apply" in result.output
        assert before == {
            p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*.md")
        }


@pytest.mark.parametrize("protected", ["deep/index.md", "index.md"])
def test_protected_paths_remain_unchanged(tmp_path: Path, protected: str) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
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
    assert "To apply" not in preview.output
    assert "may break links" not in preview.output
    assert "Keep deep/index.md:" in applied.output
    assert child.read_text().startswith("# deep")


def test_protected_missing_designated_descendant_refuses_before_deletions(
    tmp_path: Path,
) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings

    root = tmp_path / "vault"
    for folder in ("a", "b", "c"):
        (root / folder).mkdir(parents=True)
        (root / folder / "note.md").write_text("# Note\n")
    root_text = (
        '---\nokf_version: "0.3"\ntitle: Bundle\ndescription: Test bundle\nlicense: CC-BY-4.0\n---\n'
        "# Root\n\n- [Note](/a/note.md)\n- [b/](/b/index.md)\n- [c/](/c/index.md)\n"
    )
    (root / "index.md").write_text(root_text)
    for folder in ("b", "c"):
        (root / folder / "index.md").write_text(
            f"# {folder}\n\n- [Note](/{folder}/note.md)\n"
        )
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".", "a")
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=caps,
            protected_paths=["a/index.md"],
        ),
    )
    try:
        vault.index.build_index()
        preview = vault.writer.okf_collapse_indexes()
        applied = vault.writer.okf_collapse_indexes(apply=True)
        assert preview.removable == applied.removable == ()
        assert all("a/index.md" in reason for _, reason in preview.kept)
        assert (root / "index.md").read_text() == root_text
        for folder in ("b", "c"):
            assert path_exists(root / folder / "index.md")
    finally:
        vault.close()


def test_refresh_failure_reports_pending_before_deletions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp._okf_index_collapse import apply_collapse, plan_collapse
    from markdown_vault_mcp.exceptions import MarkdownMCPError

    root = tmp_path / "vault"
    for area in ("a", "b"):
        folder = root / area / "deep"
        folder.mkdir(parents=True)
        (folder / "note.md").write_text("# Note\n")
        (folder / "index.md").write_text(f"# deep\n\n- [Note](/{area}/deep/note.md)\n")
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(root, (".", "a", "b"))
    try:
        vault.index.build_index()
        plan = plan_collapse(vault._doc_mgr, vault._okf_migrate)

        def fail(*_args: object, **_kwargs: object) -> object:
            raise OSError("disk full")

        monkeypatch.setattr(vault._okf_migrate, "generate_index", fail)
        with pytest.raises(MarkdownMCPError, match=r"a/deep/index\.md"):
            apply_collapse(vault._doc_mgr, vault._okf_migrate, plan)
        assert all(path_exists(root / area / "deep/index.md") for area in ("a", "b"))
    finally:
        vault.close()


def test_new_designated_parents_reconnect_root(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    header = '---\nokf_version: "0.3"\ntitle: Bundle\ndescription: Test\nlicense: LICENSE\n---\n'
    (root / "a/deep").mkdir(parents=True)
    (root / "b/deep").mkdir(parents=True)
    (root / "index.md").write_text(
        header + "# Root\n\n- [a](/a/deep/index.md)\n- [b](/b/deep/index.md)\n"
    )
    for area in ("a", "b"):
        (root / area / "deep/note.md").write_text("# Note\n")
        (root / area / "deep/index.md").write_text(
            f"# deep\n\n- [Note](/{area}/deep/note.md)\n"
        )
    vault = designated_vault(root, (".", "a", "b"))
    try:
        vault.index.build_index()
        preview = vault.writer.okf_collapse_indexes()
        assert set(preview.refresh) == {"index.md", "a/index.md", "b/index.md"}
        result = vault.writer.okf_collapse_indexes(apply=True)
        assert set(result.refresh) == set(preview.refresh)
        body = (root / "index.md").read_text()
        assert body.startswith(header)
        assert "/a/index.md" in body and "/b/index.md" in body
        assert "/a/deep/index.md" not in body and "/b/deep/index.md" not in body
        assert "/a/deep/note.md" in (root / "a/index.md").read_text()
        assert "/b/deep/note.md" in (root / "b/index.md").read_text()
        assert vault.writer.okf_collapse_indexes().removable == ()
    finally:
        vault.close()


def test_interrupted_generator_reports_possible_partial_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp._okf_index_collapse import (
        CollapseInterrupted,
        apply_collapse,
        plan_collapse,
    )

    root = tmp_path / "vault"
    (root / "a/deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "a/deep/note.md").write_text("# Note\n")
    (root / "a/deep/index.md").write_text("# deep\n\n- [Note](/a/deep/note.md)\n")
    vault = designated_vault(root, (".", "a"))
    try:
        vault.index.build_index()
        plan = plan_collapse(vault._doc_mgr, vault._okf_migrate)
        original = vault._doc_mgr.replace_body
        calls: list[str] = []

        def fail_second(path: str, *args: object, **kwargs: object) -> bool:
            calls.append(path)
            if len(calls) == 2:
                raise OSError("disk full")
            return original(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(vault._doc_mgr, "replace_body", fail_second)
        with pytest.raises(CollapseInterrupted, match="possibly written") as caught:
            apply_collapse(vault._doc_mgr, vault._okf_migrate, plan)
        assert calls == ["a/index.md", "index.md"]
        assert path_exists(root / "a/index.md")
        assert path_exists(root / "a/deep/index.md")
        assert {"a/index.md", "index.md", "a/deep/index.md"} <= set(
            caught.value.pending
        )
        monkeypatch.setattr(vault._doc_mgr, "replace_body", original)
        result = vault.writer.okf_collapse_indexes(apply=True)
        assert result.removable and not path_exists(root / "a/deep/index.md")
    finally:
        vault.close()


def test_preview_and_apply_three_level_chain(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "a/b/c").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "a/index.md").write_text("# a\n\n- [b/](/a/b/index.md)\n")
    (root / "a/b/index.md").write_text("# b\n\n- [c/](/a/b/c/index.md)\n")
    (root / "a/b/c/index.md").write_text("# c\n\n- [Note](/a/b/c/note.md)\n")
    (root / "a/b/c/note.md").write_text("# Note\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        before = {p: p.read_bytes() for p in root.rglob("*.md")}
        preview = vault.writer.okf_collapse_indexes()
        assert len(preview.removable) == 3
        assert preview.refresh == ("index.md",)
        assert before == {p: p.read_bytes() for p in root.rglob("*.md")}
        result = vault.writer.okf_collapse_indexes(apply=True)
        assert result.refresh == preview.refresh
        assert len(result.removable) == 3
        assert "/a/b/c/note.md" in (root / "index.md").read_text()
        assert not vault.writer.okf_collapse_indexes().removable
    finally:
        vault.close()


def test_inactive_designation_is_refused(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.vault import Vault, VaultSettings

    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text("# Root\n")
    vault = Vault(
        source_dir=root, settings=VaultSettings(read_only=False, okf_mode="off")
    )
    try:
        with pytest.raises(InvalidRequestError, match="MARKDOWN_VAULT_MCP_OKF_FOLDERS"):
            vault.writer.okf_collapse_indexes()
    finally:
        vault.close()


def test_legacy_multi_note_generator_order_is_preserved(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "deep/alpha.md").write_text("# Alpha\n")
    (root / "deep/beta.md").write_text("# Beta\n")
    (root / "deep/index.md").write_text(
        "# deep\n\n- [Alpha](/deep/alpha.md)\n- [Beta](/deep/beta.md)\n"
    )
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        assert [
            path for path, _, _ in vault.writer.okf_collapse_indexes().removable
        ] == ["deep/index.md"]
    finally:
        vault.close()


@pytest.mark.parametrize("unreadable", [False, True])
def test_preview_keeps_candidates_when_root_cannot_be_refreshed(
    tmp_path: Path, unreadable: bool
) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    root_index = root / "index.md"
    root_index.write_bytes(b"\xff" if unreadable else b"# Root\n")
    (root / "deep/note.md").write_text("# Note\n")
    child = root / "deep/index.md"
    child.write_text("# deep\n\n- [Note](/deep/note.md)\n")
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=(".",)
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=caps,
            protected_paths=[] if unreadable else ["index.md"],
        ),
    )
    try:
        vault.index.build_index()
        before = root_index.read_bytes()
        preview = vault.writer.okf_collapse_indexes()
        assert preview.removable == () and preview.refresh == ()
        assert preview.kept[0][0] == "deep/index.md"
        result = vault.writer.okf_collapse_indexes(apply=True)
        assert result.kept == preview.kept
        assert child.read_text().startswith("# deep")
        assert root_index.read_bytes() == before
    finally:
        vault.close()


def test_preview_names_indexless_designated_descendant(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "legacy").mkdir(parents=True)
    (root / "projects/x").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "legacy/index.md").write_text("# legacy\n\n- [Note](/legacy/note.md)\n")
    (root / "legacy/note.md").write_text("# Note\n")
    (root / "projects/x/note.md").write_text("# Project\n")
    vault = designated_vault(root, (".", "projects/*"))
    try:
        vault.index.build_index()
        preview = vault.writer.okf_collapse_indexes()
        assert set(preview.refresh) == {"index.md", "projects/x/index.md"}
        assert not path_exists(root / "projects/x/index.md")
        result = vault.writer.okf_collapse_indexes(apply=True)
        assert set(result.refresh) == set(preview.refresh)
        assert path_exists(root / "projects/x/index.md")
    finally:
        vault.close()


def test_preview_refuses_only_protected_owner(tmp_path: Path) -> None:
    from markdown_vault_mcp.config_sections.content import ContentConfig
    from markdown_vault_mcp.vault import Vault, VaultSettings

    root = tmp_path / "vault"
    for area in ("a", "b"):
        (root / area / "deep").mkdir(parents=True)
        (root / area / "deep/note.md").write_text("# Note\n")
        (root / area / "deep/index.md").write_text(
            f"# deep\n\n- [Note](/{area}/deep/note.md)\n"
        )
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    caps = ContentConfig(
        okf_mode="on", okf_index=True, okf_folders=("a", "b")
    ).okf_capabilities
    vault = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            okf_mode="on",
            okf_capabilities=caps,
            protected_paths=["a/index.md"],
        ),
    )
    try:
        vault.index.build_index()
        plan = vault.writer.okf_collapse_indexes()
        assert [path for path, _, _ in plan.removable] == ["b/deep/index.md"]
        assert plan.refresh == ("b/index.md",)
        assert plan.kept[0][0] == "a/deep/index.md"
        assert "a/index.md" in plan.kept[0][1]
        result = vault.writer.okf_collapse_indexes(apply=True)
        assert result.kept == plan.kept
        assert path_exists(root / "a/deep/index.md")
        assert not path_exists(root / "b/deep/index.md")
    finally:
        vault.close()


def test_collapse_waits_for_index_outside_write_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Iterator
    from contextlib import contextmanager

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "deep/index.md").write_text("# deep\n\n- [Note](/deep/note.md)\n")
    (root / "deep/note.md").write_text("# Note\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        scope = vault._doc_mgr.write_scope
        ensure = vault._doc_mgr.ensure_index_current
        depth = 0

        @contextmanager
        def tracked_scope() -> Iterator[None]:
            nonlocal depth
            with scope():
                depth += 1
                try:
                    yield
                finally:
                    depth -= 1

        def checked_sync() -> None:
            assert depth == 0
            ensure()

        monkeypatch.setattr(vault._doc_mgr, "write_scope", tracked_scope)
        monkeypatch.setattr(vault._doc_mgr, "ensure_index_current", checked_sync)
        vault.writer.okf_collapse_indexes(apply=True)
    finally:
        vault.close()


def test_changed_parent_boundary_keeps_nested_candidates(tmp_path: Path) -> None:
    from markdown_vault_mcp._okf_index_collapse import apply_collapse, plan_collapse

    root = tmp_path / "vault"
    (root / "a/b").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    parent = root / "a/index.md"
    parent.write_text("# a\n\n- [b/](/a/b/index.md)\n")
    (root / "a/b/index.md").write_text("# b\n\n- [Note](/a/b/note.md)\n")
    (root / "a/b/note.md").write_text("# Note\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        plan = plan_collapse(vault._doc_mgr, vault._okf_migrate)
        parent.write_text("# Authored now\n")
        result = apply_collapse(vault._doc_mgr, vault._okf_migrate, plan)
        assert result.removable == ()
        assert ("a/index.md", "changed since preview") in result.kept
        assert ("a/b/index.md", "below a kept index") in result.kept
        assert "/a/index.md" in (root / "index.md").read_text()
        assert path_exists(root / "a/b/index.md")
    finally:
        vault.close()


def test_cli_reports_interruption_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app
    from markdown_vault_mcp.managers.document import DocumentManager

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "deep/index.md").write_text("# deep\n\n- [Note](/deep/note.md)\n")
    (root / "deep/note.md").write_text("# Note\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_INDEX", "true")

    def fail_write(*_args: object, **_kwargs: object) -> bool:
        raise OSError("disk full")

    monkeypatch.setattr(DocumentManager, "replace_body", fail_write)
    result = CliRunner().invoke(
        app, ["okf", "collapse-indexes", "--source-dir", str(root), "--apply"]
    )
    assert result.exit_code == 1
    assert "disk full" in result.output
    assert "Pending or possibly changed deep/index.md" in result.output
    assert "Pending or possibly changed index.md" in result.output
    assert "Traceback" not in result.output
    assert path_exists(root / "deep/index.md")


def test_unreadable_candidate_is_kept(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "deep/index.md").write_bytes(b"\xff")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_collapse_indexes()
        assert result.removable == ()
        assert ("deep/index.md", "unreadable") in result.kept
    finally:
        vault.close()


def test_cli_reports_removed_path_when_reconciliation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app
    from markdown_vault_mcp.managers.document import DocumentManager

    root = tmp_path / "vault"
    (root / "index.md").parent.mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    for area in ("a", "b"):
        (root / area).mkdir()
        (root / area / "index.md").write_text(
            f"# {area}\n\n- [Note](/{area}/note.md)\n"
        )
        (root / area / "note.md").write_text("# Note\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_INDEX", "true")
    delete = DocumentManager.delete
    replace = DocumentManager.replace_body
    calls = 0

    def change_then_delete(
        self: DocumentManager,
        path: str,
        if_match: str | None = None,
        *,
        allow_protected: bool = False,
    ) -> object:
        if path == "b/index.md":
            (root / path).write_text("# Changed\n")
        return delete(self, path, if_match=if_match, allow_protected=allow_protected)

    def fail_second(
        self: DocumentManager, path: str, *args: object, **kwargs: object
    ) -> bool:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        return replace(self, path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(DocumentManager, "delete", change_then_delete)
    monkeypatch.setattr(DocumentManager, "replace_body", fail_second)
    result = CliRunner().invoke(
        app, ["okf", "collapse-indexes", "--source-dir", str(root), "--apply"]
    )
    assert result.exit_code == 1
    assert "Regenerated owner ." in result.output
    assert "Removed a/index.md (any frontmatter in it is removed too)" in result.output
    assert "Pending or possibly changed b/index.md" in result.output
    assert "disk full" in result.output
    assert not path_exists(root / "a/index.md")
    assert (root / "b/index.md").read_text() == "# Changed\n"


def test_cli_reports_inactive_designation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text("# Root\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "off")
    result = CliRunner().invoke(app, ["okf", "collapse-indexes"])
    assert result.exit_code == 1
    assert "MARKDOWN_VAULT_MCP_OKF_FOLDERS" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("directory_alias", [False, True])
def test_symlink_index_kept_as_boundary(tmp_path: Path, directory_alias: bool) -> None:
    root = tmp_path / "vault"
    (root / "a").mkdir(parents=True)
    (root / "index.md").write_text("# Root\n")
    (root / "a/index.md").write_text("# b\n\n- [Note](/b/note.md)\n")
    if directory_alias:
        (root / "b").symlink_to("a", target_is_directory=True)
    else:
        (root / "b").mkdir()
        (root / "b/index.md").symlink_to("../a/index.md")
    (root / "b/note.md").write_text("# Note\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        plan = vault.writer.okf_collapse_indexes()
        assert ("b/index.md", "alias") in plan.kept
        vault.writer.okf_collapse_indexes(apply=True)
        assert path_exists(root / "a/index.md")
        assert path_exists(root / "b/index.md")
    finally:
        vault.close()


def test_retargeted_index_kept_and_reconciled(tmp_path: Path) -> None:
    from markdown_vault_mcp._okf_index_collapse import apply_collapse, plan_collapse

    root = tmp_path / "vault"
    for folder in ("a", "b"):
        (root / folder).mkdir(parents=True)
        (root / folder / "index.md").write_text(
            f"# {folder}\n\n- [Note](/{folder}/note.md)\n"
        )
        (root / folder / "note.md").write_text("# Note\n")
    (root / "index.md").write_text("# Root\n")
    vault = designated_vault(root, (".", "a"))
    try:
        vault.index.build_index()
        plan = plan_collapse(vault._doc_mgr, vault._okf_migrate)
        (root / "b/index.md").unlink()
        (root / "b/index.md").symlink_to("../a/index.md")
        result = apply_collapse(vault._doc_mgr, vault._okf_migrate, plan)
        assert ("b/index.md", "changed since preview") in result.kept
        assert path_exists(root / "a/index.md")
        assert path_exists(root / "b/index.md")
        assert "/b/index.md" in (root / "index.md").read_text()
    finally:
        vault.close()


def test_intervening_note_write_reconciles_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp._okf_write import OkfWriteIntent, okf_write_intent

    root = tmp_path / "vault"
    root.mkdir()
    header = b'---\r\nokf_version: "0.3"\r\ntitle: Bundle\r\nlicense: CC0\r\n---\r\n'
    (root / "index.md").write_bytes(header + b"# Root\n")
    for folder in ("a", "b"):
        (root / folder).mkdir()
        (root / folder / "note.md").write_text("# Original\n")
        (root / folder / "index.md").write_text(
            f"# {folder}\n\n- [Original](/{folder}/note.md)\n"
        )
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        generate = vault._okf_migrate.generate_index
        changed = False

        def generate_then_write(*args: object, **kwargs: object) -> object:
            nonlocal changed
            result = generate(*args, **kwargs)
            if kwargs.get("preview") is None and not changed:
                changed = True
                with okf_write_intent(OkfWriteIntent()):
                    vault.writer.write("b/note.md", "# Changed\n")
            return result

        monkeypatch.setattr(vault._okf_migrate, "generate_index", generate_then_write)
        vault.writer.okf_collapse_indexes(apply=True)
        assert (root / "index.md").read_bytes().startswith(header)
        body = (root / "index.md").read_text()
        assert "/a/note.md" in body and "[Changed](/b/note.md)" in body
        assert "/a/index.md" not in body and "/b/index.md" not in body
        assert path_exists(root / "a/note.md") and path_exists(root / "b/note.md")
    finally:
        vault.close()


def test_oversized_generated_index_can_collapse(tmp_path: Path) -> None:
    from markdown_vault_mcp.exceptions import NoteTooLargeError

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text("# Root\n")
    (root / "deep/note.md").write_text("# Note\n")
    (root / "deep/index.md").write_text("# deep\n\n- [Note](/deep/note.md)\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        vault._doc_mgr._max_note_read_bytes = 20
        with pytest.raises(NoteTooLargeError):
            vault._doc_mgr.read("deep/index.md")
        plan = vault.writer.okf_collapse_indexes()
        assert [path for path, _, _ in plan.removable] == ["deep/index.md"]
        result = vault.writer.okf_collapse_indexes(apply=True)
        assert [path for path, _, _ in result.removable] == ["deep/index.md"]
        assert not path_exists(root / "deep/index.md")
    finally:
        vault.close()
