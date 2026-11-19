"""Migration of descendant logs into designated ancestors."""

import contextlib
from pathlib import Path

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
        assert text.index("2025-02-01") < text.index("2025-01-01")
        assert not path_exists(root / "deep/log.md")
    finally:
        vault.close()


def test_cli_reports_malformed_source(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "vault"
    (root / "bad").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "bad/log.md").write_text("not a log\n")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_INDEX", "true")
    result = CliRunner().invoke(app, ["okf", "merge-logs", "--source-dir", str(root)])
    assert result.exit_code == 0, result.output
    assert "bad/log.md" in result.output
    assert path_exists(root / "bad/log.md")


def test_routing_headers_and_unsafe_sources(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    for folder in ("projects/a/deep", "projects/a/unsafe", "other"):
        (root / folder).mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "projects/a/log.md").write_text(
        "---\ntitle: existing\n---\n# Log\n\n## 2024-01-01\n\n- target\n"
    )
    (root / "projects/a/deep/log.md").write_text(
        "---\ntitle: source\n---\n# Log\n\n## 2025-01-01\n\n- entry\n"
    )
    (root / "projects/a/unsafe/log.md").write_text(
        "# Log\n\n## 2025-01-01\n\n- [link](note.md)\n"
    )
    (root / "other/log.md").write_text("# Log\n\n## 2026-01-01\n\n- other\n")
    vault = designated_vault(root, ("projects/*",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_merge_logs(apply=True)
        assert {(m.source, m.target) for m in result.merged} == {
            ("projects/a/deep/log.md", "projects/a/log.md"),
            ("other/log.md", "log.md"),
            ("projects/a/unsafe/log.md", "projects/a/log.md"),
        }
        assert result.target_frontmatter == ("projects/a/log.md",)
        assert result.source_frontmatter == ("projects/a/deep/log.md",)
        target = (root / "projects/a/log.md").read_text()
        assert target.startswith("---\ntitle: existing\n---")
        assert "title: source" not in target
        assert "[link](/projects/a/unsafe/note.md)" in target
        assert not path_exists(root / "projects/a/unsafe/log.md")
    finally:
        vault.close()


def test_refuses_incompatible_preamble_and_reference_links(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    for name in ("different", "reference", "fragment"):
        (root / name).mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "log.md").write_text("# Log\n\n## 2025-01-01\n\n- target\n")
    (root / "different/log.md").write_text("# Other\n\n## 2025-01-01\n\n- source\n")
    (root / "reference/log.md").write_text(
        "# Log\n\n[ref]: note.md\n\n## 2025-01-01\n\n- [source][ref]\n"
    )
    (root / "fragment/log.md").write_text(
        "# Log\n\n## 2025-01-01\n\n- [source](#section)\n"
    )
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_merge_logs(apply=True)
        assert {move.source for move in result.merged} == {
            "different/log.md",
            "reference/log.md",
            "fragment/log.md",
        }
        assert not result.kept
        assert "# Other" in (root / "log.md").read_text()
        assert "[ref]: /reference/note.md" in (root / "log.md").read_text()
        assert "[source](#section)" in (root / "log.md").read_text()
    finally:
        vault.close()


def test_interrupted_delete_reports_target_may_have_changed(
    tmp_path: Path, monkeypatch
) -> None:
    import pytest

    from markdown_vault_mcp.exceptions import InvalidRequestError

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "deep/log.md").write_text("# Log\n\n## 2025-01-01\n\n- source\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()

        def fail_delete(*_args, **_kwargs):
            raise OSError("delete failed")

        monkeypatch.setattr(vault._doc_mgr, "delete", fail_delete)
        with pytest.raises(InvalidRequestError, match="may have changed"):
            vault.writer.okf_merge_logs(apply=True)
        assert path_exists(root / "deep/log.md")
        assert "- source" in (root / "log.md").read_text()
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
        "---\ntitle: source\n---\n# Log\n\n## 2025-01-01\n\n- source\n"
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    args = ["okf", "merge-logs", "--source-dir", str(root)]
    preview = CliRunner().invoke(app, args)
    assert preview.exit_code == 0, preview.output
    assert "deep/log.md -> log.md: 1 entries" in preview.output
    assert "Source frontmatter encountered" in preview.output
    assert path_exists(root / "deep/log.md")
    applied = CliRunner().invoke(app, [*args, "--apply"])
    assert applied.exit_code == 0, applied.output
    assert "Merged deep/log.md" in applied.output
    assert not path_exists(root / "deep/log.md")
    assert (root / "log.md").read_text().startswith("# Log")


def test_migration_keeps_non_ascii_whitespace_inside_entry() -> None:
    from markdown_vault_mcp._okf_log_merge import _migration_text, _parse

    target = "# Log\n\n## 2025-01-01\n\n- a\n\u00a0\n"
    source = "# Log\n\n## 2025-01-01\n\n- b\n"
    merged, count, reason = _migration_text(target, source)
    assert merged is not None, reason
    parsed, reason = _parse(merged)
    assert parsed is not None, reason
    assert count == 1
    assert parsed.sections[0].entries == [("- a", "\u00a0"), ("- b",)]


def test_concurrent_target_creation_preserves_both_files(
    tmp_path: Path, monkeypatch
) -> None:
    from markdown_vault_mcp.exceptions import MarkdownMCPError

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    source = root / "deep/log.md"
    source.write_text("# Log\n\n## 2025-01-01\n\n- source\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        write = vault._doc_mgr.write

        def concurrent_write(*args, **kwargs):
            (root / "log.md").write_text("external data\n")
            return write(*args, **kwargs)

        monkeypatch.setattr(vault._doc_mgr, "write", concurrent_write)
        with contextlib.suppress(MarkdownMCPError):
            vault.writer.okf_merge_logs(apply=True)
        assert (root / "log.md").read_text() == "external data\n"
        assert source.read_text() == "# Log\n\n## 2025-01-01\n\n- source\n"
    finally:
        vault.close()


def test_target_created_at_publication_preserves_both(
    tmp_path: Path, monkeypatch
) -> None:
    from markdown_vault_mcp.exceptions import MarkdownMCPError
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
        with contextlib.suppress(MarkdownMCPError):
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


def test_retry_after_delete_failure_preserves_multiplicity(
    tmp_path: Path, monkeypatch
) -> None:
    import pytest

    from markdown_vault_mcp.exceptions import InvalidRequestError

    for existing in (False, True):
        root = tmp_path / str(existing)
        (root / "deep").mkdir(parents=True)
        (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
        original = "# Log\n\n## 2024-01-01\n\n- old\n"
        if existing:
            (root / "log.md").write_text(original)
        (root / "deep/log.md").write_text(
            "# Log\n\n## 2025-01-01\n\n- repeat\n- repeat\n"
        )
        vault = designated_vault(root, (".",))
        try:
            vault.index.build_index()
            real_delete = vault._doc_mgr.delete

            def fail_source(path, *args, _delete=real_delete, **kwargs):
                if path == "deep/log.md":
                    raise PermissionError("denied")
                return _delete(path, *args, **kwargs)

            monkeypatch.setattr(vault._doc_mgr, "delete", fail_source)
            with pytest.raises(InvalidRequestError):
                vault.writer.okf_merge_logs(apply=True)
            if existing:
                assert (root / "log.md").read_text() == original
            else:
                assert not path_exists(root / "log.md")
            monkeypatch.setattr(vault._doc_mgr, "delete", real_delete)
            vault.writer.okf_merge_logs(apply=True)
            assert (root / "log.md").read_text().count("- repeat") == 2
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
        assert "[note](/deep/note.md#part)" in target
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


def test_failed_delete_does_not_rollback_changed_source(
    tmp_path: Path, monkeypatch
) -> None:
    import pytest

    from markdown_vault_mcp.exceptions import InvalidRequestError

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    source = root / "deep/log.md"
    source.write_text("# Log\n\n## 2025-01-01\n\n- source\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()

        def fail_after_change(_path, *_args, **_kwargs):
            source.write_text("changed externally\n")
            raise PermissionError("denied")

        monkeypatch.setattr(vault._doc_mgr, "delete", fail_after_change)
        with pytest.raises(InvalidRequestError, match="target may have changed"):
            vault.writer.okf_merge_logs(apply=True)
        assert "- source" in (root / "log.md").read_text()
        assert source.read_text() == "changed externally\n"
    finally:
        vault.close()


def test_relocates_explicit_relative_wikilink(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "deep/log.md").write_text(
        "# Log\n\n## 2025-01-01\n\n- [[./note#part|Name]]\n"
    )
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        result = vault.writer.okf_merge_logs(apply=True)
        assert len(result.merged) == 1
        assert "[[deep/note#part|Name]]" in (root / "log.md").read_text()
    finally:
        vault.close()


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


def test_failed_delete_does_not_rollback_changed_target(
    tmp_path: Path, monkeypatch
) -> None:
    import pytest

    from markdown_vault_mcp.exceptions import InvalidRequestError

    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    source = root / "deep/log.md"
    source.write_text("# Log\n\n## 2025-01-01\n\n- source\n")
    vault = designated_vault(root, (".",))
    try:
        vault.index.build_index()
        real_delete = vault._doc_mgr.delete

        def fail_after_change(path, *args, **kwargs):
            if path == "deep/log.md":
                (root / "log.md").write_text("changed externally\n")
                raise PermissionError("denied")
            return real_delete(path, *args, **kwargs)

        monkeypatch.setattr(vault._doc_mgr, "delete", fail_after_change)
        with pytest.raises(InvalidRequestError, match="target may have changed"):
            vault.writer.okf_merge_logs(apply=True)
        assert (root / "log.md").read_text() == "changed externally\n"
        assert "- source" in source.read_text()
    finally:
        vault.close()


def test_new_older_date_has_blank_separator() -> None:
    from markdown_vault_mcp._okf_log_merge import _migration_text

    merged, _, reason = _migration_text(
        "# Log\n\n## 2025-01-01\n\n- old\n",
        "# Log\n\n## 2024-01-01\n\n- older\n",
    )
    assert merged is not None, reason
    assert "- old\n\n## 2024-01-01" in merged


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
        assert "[this](/log.md#2025-01-01)" in result
        assert "[section](#2025-01-01)" in result
    finally:
        vault.close()


def test_images_and_html_are_merged_with_notes(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "vault"
    (root / "deep").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Root\n')
    (root / "deep/log.md").write_text(
        "# Log\n\n[img]: pictures/a.png\n\n## 2025-01-01\n\n"
        '- ![inline](a.png)\n- ![reference][img]\n- <img src="a.png"> <br>\n'
    )
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_MODE", "on")
    args = ["okf", "merge-logs", "--source-dir", str(root)]
    preview = CliRunner().invoke(app, args)
    assert preview.exit_code == 0, preview.output
    assert "relative image URL not rewritten; check after merge" in preview.output
    assert "HTML URL not rewritten; check after merge" in preview.output
    applied = CliRunner().invoke(app, [*args, "--apply"])
    assert applied.exit_code == 0, applied.output
    assert "relative image URL not rewritten" in applied.output
    text = (root / "log.md").read_text()
    assert "![inline](a.png)" in text and '<img src="a.png"> <br>' in text
    assert "[img]: /deep/pictures/a.png" in text
    assert not path_exists(root / "deep/log.md")


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


def test_plain_html_does_not_raise_relocation_note() -> None:
    from markdown_vault_mcp._okf_log_merge import _relocation_notes

    assert not _relocation_notes(
        "# Log\n\n## 2025-01-01\n\n- <br> <kbd>key</kbd> <!-- src=x -->\n"
    )
