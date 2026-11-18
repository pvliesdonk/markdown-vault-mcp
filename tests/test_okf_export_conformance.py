"""Conformance is assessed on the bytes that leave the exporter."""

from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    ("name", "data", "verdict", "reason"),
    [
        ("note.md", b"---\ntype: Thing\n---\n# Note", "conformant", None),
        (
            "note.md",
            b"---\ntype: '  '\n---\n",
            "nonconformant",
            "missing_or_invalid_type",
        ),
        ("note.md", b"---\ntype: [\n---\n", "nonconformant", "malformed_frontmatter"),
        (
            "note.md",
            b'{"type": "Thing"}\n',
            "nonconformant",
            "missing_yaml_frontmatter",
        ),
        ("index.md", b"---\nokf_version: '0.2'\n---\n# Index\n", "conformant", None),
        (
            "index.md",
            b"---\ntitle: Index\n---\n# Index\n",
            "nonconformant",
            "index_frontmatter",
        ),
        (
            "sub/index.md",
            b"---\nokf_version: '0.2'\n---\n# Index\n",
            "nonconformant",
            "index_frontmatter",
        ),
        ("index.md", b"# Index\n* broken\n", "nonconformant", "index_entry_shape"),
        ("log.md", b"# Log\n## 2026-02-30\n", "nonconformant", "log_heading_shape"),
        ("log.md", b"# Log\n## 2026-02-28\n", "conformant", None),
    ],
)
def test_format_rules(name: str, data: bytes, verdict: str, reason: str | None) -> None:
    report = _audit({name: data, "other.unknown": b"anything"})
    assert report.verdict == verdict
    assert reason is None or any(f.reason == reason for f in report.format_findings)


def test_advisories_and_broken_links_do_not_change_verdict() -> None:
    report = _audit({"note.md": b"---\ntype: Unknown\n---\n[missing](missing.md)"})
    assert report.verdict == "conformant"
    assert report.optional_metadata == ["note.md"]
    assert [(item.path, item.target) for item in report.broken_links] == [
        ("note.md", "missing.md")
    ]


def test_scope_index_is_checked_at_archive_root(tmp_path: Path) -> None:
    folder = tmp_path / "nested"
    folder.mkdir()
    (folder / "index.md").write_text("---\nokf_version: '0.2'\n---\n# Index\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="nested"
        )
    finally:
        vault.close()
    assert result.report.verdict == "conformant"
