"""Conformance is assessed on the bytes that leave the exporter."""

from pathlib import Path

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.okf_bundle import build_okf_bundle
from markdown_vault_mcp.vault import Vault, VaultSettings


def test_export_reports_missing_frontmatter_from_archive(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Read me\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    assert result.report.verdict == "nonconformant"
    assert any(f.path == "README.md" for f in result.report.format_findings)


def _audit(files: dict[str, bytes]):
    import io
    import zipfile

    from markdown_vault_mcp.okf_export_conformance import audit_export
    from markdown_vault_mcp.okf_export_report import OkfExportReport

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    report = OkfExportReport()
    audit_export(stream.getvalue(), report)
    return report


def test_index_body_checks_structure_and_ignores_fenced_examples() -> None:
    from markdown_vault_mcp.okf import build_index_markdown

    valid = build_index_markdown("Index", [("A", "/a.md", "A note")])
    assert _audit({"index.md": valid.encode()}).verdict == "conformant"
    assert _audit({"index.md": b"# Index\nPlain prose\n"}).verdict == "nonconformant"
    assert (
        _audit({"index.md": b"# Index\n```\n* [A](a.md)\n```\n"}).verdict
        == "nonconformant"
    )


def test_log_checks_headings_not_fenced_examples() -> None:
    assert (
        _audit({"log.md": b"# Log\n```\n## Yesterday\n```\n"}).verdict == "conformant"
    )
    assert _audit({"log.md": b"# Log\n## Yesterday\n"}).verdict == "nonconformant"


def test_unchecked_takes_precedence_over_findings() -> None:
    report = _audit({"bad.md": b"No header", "unknown.md": b"\xff"})
    assert report.verdict == "incomplete"
    assert "unknown.md" in report.unchecked
    assert any(f.path == "bad.md" for f in report.format_findings)
