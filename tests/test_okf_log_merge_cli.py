"""Migration of descendant logs into designated ancestors."""

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
        }
        assert result.target_frontmatter == ("projects/a/log.md",)
        assert result.source_frontmatter == ("projects/a/deep/log.md",)
        target = (root / "projects/a/log.md").read_text()
        assert target.startswith("---\ntitle: existing\n---")
        assert "title: source" not in target
        assert dict(result.kept)["projects/a/unsafe/log.md"] == "relative or wiki link"
        assert path_exists(root / "projects/a/unsafe/log.md")
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
        assert not result.merged
        assert dict(result.kept) == {
            "different/log.md": "preamble differs",
            "fragment/log.md": "relative or wiki link",
            "reference/log.md": "relative or wiki link",
        }
        assert all(
            path_exists(root / name / "log.md")
            for name in ("different", "reference", "fragment")
        )
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
