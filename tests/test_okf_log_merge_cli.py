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
        assert (
            next(m for m in result.merged if m.frontmatter).source
            == "projects/a/deep/log.md"
        )
        target = (root / "projects/a/log.md").read_text()
        assert target.startswith("---\ntitle: existing\n---")
        assert "title: source" not in target
        assert dict(result.kept)["projects/a/unsafe/log.md"] == "relative or wiki link"
        assert path_exists(root / "projects/a/unsafe/log.md")
    finally:
        vault.close()
