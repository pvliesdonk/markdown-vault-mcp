"""Migration of descendant logs into designated ancestors."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from markdown_vault_mcp.cli import app
from markdown_vault_mcp.utils.fs import path_exists
from tests.fixtures.designated_index import designated_vault


def test_preview_and_apply_keep_repeated_entries(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "log.md").write_text("# Log\n\n## 2025-01-01\n\n- old\n")
    (root / "deep/log.md").write_text("# Log\n\n## 2025-02-01\n\n- same\n- same\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        preview = vault.writer.okf_merge_logs()
        assert [(x.source, x.target, x.entries) for x in preview.merged] == [
            ("deep/log.md", "log.md", 2)
        ]
        assert path_exists(root / "deep/log.md")
        vault.writer.okf_merge_logs(apply=True)
        text = (root / "log.md").read_text()
        assert text.count("- same") == 2
        assert not path_exists(root / "deep/log.md")
    finally:
        vault.close()


def test_cli_reports_malformed_source(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "vault"
    (root / "bad").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "bad/log.md").write_text("not a log\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    result = CliRunner().invoke(
        app, ["okf", "merge-logs", "--source-dir", str(root), "--apply"]
    )
    assert result.exit_code == 0, result.output
    assert "Keep bad/log.md:" in result.output
    assert path_exists(root / "bad/log.md")


def test_routing_and_header_reporting(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    for folder in ("projects/a/deep", "other"):
        (root / folder).mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "projects/a/log.md").write_text(
        "---\ntitle: existing\n---\n# Log\n\n## 2024-01-01\n\n- target\n"
    )
    (root / "projects/a/deep/log.md").write_text(
        "---\ntitle: source\n---\n# Log\n\n## 2025-01-01\n\n- entry\n"
    )
    (root / "other/log.md").write_text("# Log\n\n## 2026-01-01\n\n- other\n")
    vault = designated_vault(root, ("projects/*",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_merge_logs(apply=True)
        assert {(m.source, m.target) for m in result.merged} == {
            ("projects/a/deep/log.md", "projects/a/log.md"),
            ("other/log.md", "log.md"),
        }
        assert result.target_frontmatter == ("projects/a/log.md",)
        assert result.source_frontmatter == ("projects/a/deep/log.md",)
        target = (root / "projects/a/log.md").read_text()
        assert "title: source" not in target
    finally:
        vault.close()


def test_hidden_and_excluded_logs_are_considered(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    for folder in (".hidden", "excluded"):
        (root / folder).mkdir(parents=True)
        (root / folder / "log.md").write_text("# Log\n\n## 2025-01-01\n\n- entry\n")
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(root, (".",), exclude=("excluded/**",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_merge_logs()
        assert {move.source for move in result.merged} == {
            ".hidden/log.md",
            "excluded/log.md",
        }
    finally:
        vault.close()


def test_alias_target_is_kept(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "deep/log.md").write_text("# Log\n\n## 2025-01-01\n\n- source\n")
    (root / "log.md").symlink_to("other.md")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_merge_logs(apply=True)
        assert dict(result.kept)["deep/log.md"] == "aliased log"
        assert path_exists(root / "deep/log.md")
        assert not path_exists(root / "other.md")
    finally:
        vault.close()


def test_cli_preview_and_apply(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "deep/log.md").write_text(
        '---\ntitle: source\n---\n# Log\n\n## 2025-01-01\n\n- ![image](a.png) <img src="a.png">\n'
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    args = ["okf", "merge-logs", "--source-dir", str(root)]
    preview = CliRunner().invoke(app, args)
    assert preview.exit_code == 0, preview.output
    assert "deep/log.md -> log.md: 1 entries" in preview.output
    assert "Source frontmatter encountered" in preview.output
    assert "relative image URL not rewritten" in preview.output
    assert "HTML URL not rewritten" in preview.output
    assert path_exists(root / "deep/log.md")
    applied = CliRunner().invoke(app, [*args, "--apply"])
    assert applied.exit_code == 0, applied.output
    assert "Merged deep/log.md" in applied.output
    assert "relative image URL not rewritten" in applied.output
    assert "HTML URL not rewritten" in applied.output
    assert not path_exists(root / "deep/log.md")
    assert (root / "log.md").read_text().startswith("# Log")


def test_migration_keeps_non_ascii_whitespace_inside_entry() -> None:
    from markdown_vault_mcp._okf_log_merge import _migration_text

    target = "# Log\n\n## 2025-01-01\n\n- a\n\u00a0\n"
    source = "# Log\n\n## 2025-01-01\n\n- b\n"
    merged, count, reason = _migration_text(target, source)
    assert merged is not None, reason
    assert count == 1
    assert "- a\n\u00a0\n- b" in merged


def test_target_created_at_publication_preserves_both(
    tmp_path: Path, monkeypatch
) -> None:
    """Verify migration requests exclusive publication at the actual link boundary."""
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.managers import _write_kernel

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    source = root / "deep/log.md"
    source.write_text("# Log\n\n## 2025-01-01\n\n- source\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        link = _write_kernel.os.link

        def concurrent_link(src, dst, *args, **kwargs):
            (root / "log.md").write_text("external data\n")
            return link(src, dst, *args, **kwargs)

        monkeypatch.setattr(_write_kernel.os, "link", concurrent_link)
        with pytest.raises(InvalidRequestError, match="may have changed"):
            vault.writer.okf_merge_logs(apply=True)
        assert (root / "log.md").read_text() == "external data\n"
        assert source.read_text() == "# Log\n\n## 2025-01-01\n\n- source\n"
    finally:
        vault.close()


def test_target_frontmatter_bytes_survive(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    header = b"---\r\ntitle: 'Existing'\r\n# comment\r\n---\r\n"
    (root / "log.md").write_bytes(
        header + b"# Log\r\n\r\n## 2024-01-01\r\n\r\n- target\r\n"
    )
    (root / "deep/log.md").write_text("# Log\n\n## 2025-01-01\n\n- source\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        vault.writer.okf_merge_logs(apply=True)
        assert (root / "log.md").read_bytes().startswith(header)
    finally:
        vault.close()


@pytest.mark.parametrize(
    "scenario", ["new", "existing", "source_changed", "target_changed", "restore_fails"]
)
def test_retry_after_delete_failure_preserves_multiplicity(
    tmp_path: Path, monkeypatch, scenario: str
) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    original = "# Log\n\n## 2024-01-01\n\n- old\n"
    target = root / "log.md"
    source = root / "deep/log.md"
    if scenario == "existing":
        target.write_text(original)
    source.write_text("# Log\n\n## 2025-01-01\n\n- repeat\n- repeat\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        real_delete = vault._doc_mgr.delete

        def fail_delete(path, *args, **kwargs):
            if path == "deep/log.md":
                if scenario == "source_changed":
                    source.write_text("changed externally\n")
                if scenario == "target_changed":
                    target.write_text("changed externally\n")
                raise PermissionError("denied")
            if scenario == "restore_fails":
                raise OSError("restore failed")
            return real_delete(path, *args, **kwargs)

        monkeypatch.setattr(vault._doc_mgr, "delete", fail_delete)
        with pytest.raises(
            InvalidRequestError,
            match="target restored"
            if scenario in {"new", "existing"}
            else "target may have changed",
        ):
            vault.writer.okf_merge_logs(apply=True)
        if scenario in {"new", "existing"}:
            if scenario == "existing":
                assert target.read_text() == original
            else:
                assert not path_exists(target)
            monkeypatch.setattr(vault._doc_mgr, "delete", real_delete)
            assert len(vault.writer.okf_merge_logs(apply=True).merged) == 1
            assert not path_exists(source)
        elif scenario == "source_changed":
            assert source.read_text() == "changed externally\n"
            assert "- repeat" in target.read_text()
        elif scenario == "target_changed":
            assert target.read_text() == "changed externally\n"
            assert "- repeat" in source.read_text()
        else:
            assert "- repeat" in source.read_text()
            assert "- repeat" in target.read_text()
    finally:
        vault.close()


def test_merge_relocates_links_and_keeps_different_preamble(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "log.md").write_text(
        "# Root log\n\ntarget intro\n\n## 2024-01-01\n\n- old\n"
    )
    (root / "deep/log.md").write_text(
        "# Deep log\n\nsource intro\n\n## 2025-01-01\n\n- [note](note.md#part)\n"
    )
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_merge_logs(apply=True)
        assert len(result.merged) == 1
        target = (root / "log.md").read_text()
        assert "source intro" in target and "# Deep log" in target
        assert "[note](deep/note.md#part)" in target
        assert (
            target.index("source intro")
            < target.index("## 2025-01-01")
            < target.index("## 2024-01-01")
        )
        assert not path_exists(root / "deep/log.md")
    finally:
        vault.close()


def test_interruption_reports_completed_and_pending(
    tmp_path: Path, monkeypatch
) -> None:
    import pytest

    from markdown_vault_mcp._okf_log_merge import _LogMigrationInterrupted

    root = tmp_path / "vault"
    for name in ("a", "b", "c"):
        (root / name).mkdir(parents=True)
        (root / name / "log.md").write_text(f"# Log\n\n## 2025-01-01\n\n- {name}\n")
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        real_delete = vault._doc_mgr.delete

        def fail_b(path, *args, **kwargs):
            if path == "b/log.md":
                raise PermissionError("denied")
            return real_delete(path, *args, **kwargs)

        monkeypatch.setattr(vault._doc_mgr, "delete", fail_b)
        with pytest.raises(_LogMigrationInterrupted) as caught:
            vault.writer.okf_merge_logs(apply=True)
        assert [move.source for move in caught.value.completed] == ["a/log.md"]
        assert caught.value.pending == ("b/log.md", "c/log.md")
        assert "- a" in (root / "log.md").read_text()
        assert "- b" not in (root / "log.md").read_text()
    finally:
        vault.close()


def test_relocation_preserves_destination_spelling() -> None:
    from markdown_vault_mcp._okf_log_merge import _relocate_links

    source = (
        "[parent](../b.md#h) [sibling](sib.md) [space](<my note.md>)\n"
        "![image][img] [[./note#part|Name]]\n\n[img]: ./z%20q.md\n"
    )
    moved = _relocate_links(source, "x/y/log.md", "x/log.md")
    assert "[parent](b.md#h)" in moved
    assert "[sibling](y/sib.md)" in moved
    assert "[space](<y/my note.md>)" in moved
    assert "[img]: y/z%20q.md" in moved
    assert "[[x/y/note#part|Name]]" in moved


def test_cli_interruption_prints_progress(tmp_path: Path, monkeypatch) -> None:
    from markdown_vault_mcp.managers.document import DocumentManager

    root = tmp_path / "vault"
    for name in ("a", "b"):
        (root / name).mkdir(parents=True)
        (root / name / "log.md").write_text(f"# Log\n\n## 2025-01-01\n\n- {name}\n")
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    delete = DocumentManager.delete

    def fail_b(self, path, *args, **kwargs):
        if path == "b/log.md":
            raise PermissionError("denied")
        return delete(self, path, *args, **kwargs)

    monkeypatch.setattr(DocumentManager, "delete", fail_b)
    result = CliRunner().invoke(
        app, ["okf", "merge-logs", "--source-dir", str(root), "--apply"]
    )
    assert result.exit_code == 1
    assert "Merged a/log.md" in result.output
    assert "Pending or uncertain: b/log.md" in result.output


def test_self_log_links_point_at_target(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "deep/log.md").write_text(
        "# Log\n\n## 2025-01-01\n\n- [this](log.md#2025-01-01)\n- [section](#2025-01-01)\n"
    )
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        vault.writer.okf_merge_logs(apply=True)
        result = (root / "log.md").read_text()
        assert "[this](log.md#2025-01-01)" in result
        assert "[section](#2025-01-01)" in result
    finally:
        vault.close()


def test_reference_collision_is_reported_and_entries_are_kept(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "log.md").write_text(
        "# Log\n\n[r]: /target.md\n\n## 2024-01-01\n\n- [old][r]\n"
    )
    (root / "deep/log.md").write_text(
        "# Log\n\n[r]: note.md\n\n## 2025-01-01\n\n- [new][r]\n"
    )
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_merge_logs(apply=True)
        assert len(result.merged) == 1
        assert any(
            "source links use the target definition" in note
            for _, note in result.unrewritten
        )
        text = (root / "log.md").read_text()
        assert "- [old][r]" in text and "- [new][r]" in text
        assert not path_exists(root / "deep/log.md")
    finally:
        vault.close()


def test_relocation_notes_for_images_and_html() -> None:
    from markdown_vault_mcp._okf_log_merge import _relocation_notes

    notes = _relocation_notes('![inline](a.png) <img src="a.png">')
    assert "relative image URL not rewritten; check after merge" in notes
    assert "HTML URL not rewritten; check after merge" in notes
    assert not _relocation_notes(
        "# Log\n\n## 2025-01-01\n\n- <br> <kbd>key</kbd> <!-- src=x -->\n"
    )
