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
    audit_export(stream.getvalue(), report, frozenset({"png", "pdf"}))
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
        _audit({"log.md": b"# Log\n## 2026-01-01\n```\n## Yesterday\n```\n"}).verdict
        == "conformant"
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


def test_log_frontmatter_does_not_define_format_policy() -> None:
    report = _audit({"log.md": b"---\nbroken: [\n---\n# Log\n## 2026-11-18\n"})
    assert report.verdict == "conformant"


def test_artifact_bytes_override_stale_inventory_state(tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text("# Untyped\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        vault.index.build_index()
        path.write_text("---\ntype: Thing\n---\n# Typed\n")
        report = build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path)).report
    finally:
        vault.close()
    assert report.verdict == "conformant"
    assert not report.format_findings


def test_nested_index_list_and_unparseable_index_are_reported() -> None:
    report = _audit({"index.md": b"# Index\n- [A](a.md)\n  - [B](b.md)\n"})
    assert report.verdict == "nonconformant"
    report = _audit({"index.md": b"---\ninvalid: [\n---\n# Index\n"})
    assert report.verdict == "nonconformant"
    assert report.unchecked == {}


def test_unreadable_archive_member_names_the_unchecked_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zipfile

    def fail_read(_self: zipfile.ZipFile, *_args: object, **_kwargs: object) -> bytes:
        raise zipfile.BadZipFile("corrupt member")

    monkeypatch.setattr(zipfile.ZipFile, "read", fail_read)
    report = _audit({"note.md": b"---\ntype: Thing\n---\n"})
    assert report.verdict == "incomplete"
    assert report.unchecked["note.md"] == ["yaml_frontmatter", "type"]


@pytest.mark.parametrize(
    ("name", "checks"),
    [
        ("index.md", ["index_frontmatter", "index_entry_shape"]),
        ("log.md", ["log_date_headings"]),
    ],
)
def test_unreadable_reserved_file_names_its_checks(
    name: str, checks: list[str]
) -> None:
    report = _audit({name: b"\xff"})
    assert report.verdict == "incomplete"
    assert report.unchecked[name] == checks


def test_unparseable_index_still_checks_body() -> None:
    report = _audit({"index.md": b"---\ninvalid: [\n---\n# Index\n* broken\n"})
    assert report.verdict == "nonconformant"
    assert any(f.reason == "index_entry_shape" for f in report.format_findings)


def test_empty_index_link_is_not_an_entry() -> None:
    report = _audit({"index.md": b"# Index\n* [](a.md)\n"})
    assert report.verdict == "nonconformant"


@pytest.mark.parametrize("body", [b"# Index\n\n---\n", b"# Index\n\n> - [A](a.md)\n"])
def test_index_rejects_non_entry_blocks(body: bytes) -> None:
    assert _audit({"index.md": body}).verdict == "nonconformant"


def test_log_requires_newest_dates_first() -> None:
    report = _audit({"log.md": b"# Log\n## 2025-01-01\n- Old\n## 2026-01-01\n- New\n"})
    assert report.verdict == "nonconformant"


def test_broken_attachment_link_is_reported() -> None:
    report = _audit({"note.md": b"---\ntype: T\n---\n[manual](missing.pdf)\n"})
    assert [(item.path, item.target) for item in report.broken_links] == [
        ("note.md", "missing.pdf")
    ]


def test_malformed_frontmatter_still_reports_body_links() -> None:
    report = _audit({"note.md": b"---\ntype: [\n---\n[missing](missing.md)\n"})
    assert [(item.path, item.target) for item in report.broken_links] == [
        ("note.md", "missing.md")
    ]


@pytest.mark.parametrize("missing", ["resource", "tags"])
def test_recommended_metadata_reports_each_missing_field(missing: str) -> None:
    fields = {
        "type": "T",
        "title": "Title",
        "description": "Description",
        "resource": "/x",
        "tags": "[x]",
    }
    del fields[missing]
    header = "\n".join(f"{key}: {value}" for key, value in fields.items())
    report = _audit({"note.md": f"---\n{header}\n---\n".encode()})
    assert report.verdict == "conformant"
    assert report.optional_metadata == ["note.md"]


def test_malformed_nested_index_has_known_frontmatter_violation() -> None:
    report = _audit({"sub/index.md": b"---\nbad: [\n---\n# Index\n"})
    assert report.verdict == "nonconformant"
    assert "sub/index.md" not in report.unchecked
    assert any(f.reason == "index_frontmatter" for f in report.format_findings)


def test_design_describes_artifact_verdict() -> None:
    text = (Path(__file__).parents[1] / "docs/design/okf.md").read_text()
    assert "partial v0.2 observations" not in text
    assert "not a conformance verdict or snapshot" not in text


def test_artifact_link_observations_cover_images_references_and_encoded_paths() -> None:
    report = _audit(
        {
            "sub/note.md": b"---\ntype: T\n---\n[local](../files/a%20b.pdf?view=1#p) ![image](missing.png) [ref][id] [remote](https://example.com/no.pdf) [self](#heading)\n\n[id]: ../absent.pdf\n",
            "files/a b.pdf": b"present",
        }
    )
    assert report.verdict == "conformant"
    assert {(item.path, item.target) for item in report.broken_links} == {
        ("sub/note.md", "missing.png"),
        ("sub/note.md", "../absent.pdf"),
    }


def test_date_order_allows_equal_dates_and_descending_dates() -> None:
    report = _audit({"log.md": b"# Log\n## 2026-01-01\n## 2026-01-01\n## 2025-01-01\n"})
    assert report.verdict == "conformant"


@pytest.mark.parametrize(
    "body",
    [
        b"# Index\n-\n",
        b"# Index\n* [A](a.md)\n\n  prose\n",
        "# Index\n* [A](a.md) — desc\n".encode(),
    ],
)
def test_index_closed_entry_grammar(body: bytes) -> None:
    assert _audit({"index.md": body}).verdict == "nonconformant"


@pytest.mark.parametrize(
    "body",
    [
        b"# Log\nUngrouped entry\n",
        b"# Log\n- Ungrouped\n",
        b"# Log\n```\n## example\n```\n",
    ],
)
def test_log_requires_group_before_entries(body: bytes) -> None:
    assert _audit({"log.md": body}).verdict == "nonconformant"


def test_artifact_observer_masks_code_and_finds_wiki_attachments() -> None:
    body = b"---\ntype: T\n---\n![[missing.png]]\n\n    [[code]]\n\n`[[inline]]`\n\n```\n[[fenced]]\n```\n"
    report = _audit({"note.md": body})
    assert [(item.path, item.target) for item in report.broken_links] == [
        ("note.md", "missing.png")
    ]


def test_archive_directories_and_wiki_name_are_present() -> None:
    body = b"---\ntype: T\n---\n[root](/) [dot](.) [here](./) [[b]]\n"
    report = _audit({"note.md": body, "sub/b.md": b"---\ntype: T\n---\n"})
    assert report.broken_links == []


def test_invalid_root_index_header_is_decidable() -> None:
    report = _audit({"index.md": b"---\ninvalid: [\n---\n# Index\n"})
    assert report.verdict == "nonconformant"
    assert report.unchecked == {}
    assert [f.reason for f in report.format_findings] == ["index_frontmatter"]


def test_invalid_encoding_only_records_unchecked() -> None:
    report = _audit({"note.md": b"\xff"})
    assert report.verdict == "incomplete"
    assert report.format_findings == []


def test_missing_wikilinks_and_relative_destinations_report_written_targets() -> None:
    body = b"---\ntype: T\n---\n[[missing]] ![[lost.png]] [[../absent]] [outside](../outside.pdf)\n"
    report = _audit({"sub/note.md": body})
    assert [item.target for item in report.broken_links] == [
        "missing",
        "lost.png",
        "../absent",
        "../outside.pdf",
    ]
    assert report.verdict == "conformant"


def test_custom_attachment_extension_uses_same_name_in_export(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text("---\ntype: T\n---\n![[missing.custom]]\n")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(attachment_extensions=["custom"])
    )
    try:
        report = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path, attachment_extensions=("custom",))
        ).report
    finally:
        vault.close()
    assert [item.target for item in report.broken_links] == ["missing.custom"]


def test_log_allows_entry_content_after_date_and_title_only() -> None:
    assert _audit({"log.md": b"# Log\n"}).verdict == "conformant"
    assert (
        _audit(
            {"log.md": b"# Log\n## 2026-01-01\nEntry\n```\n## sample\n```\n"}
        ).verdict
        == "conformant"
    )


def test_omitted_dependency_matches_artifact_observation(tmp_path: Path) -> None:
    folder = tmp_path / "guides"
    folder.mkdir()
    (folder / "note.md").write_text("---\ntype: T\n---\n[x](/outside.md)\n")
    (tmp_path / "outside.md").write_text("---\ntype: T\n---\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        report = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        ).report
    finally:
        vault.close()
    assert [item.target for item in report.dependencies] == ["/outside.md"]
    assert [item.target for item in report.broken_links] == ["/outside.md"]


def test_real_export_accepts_generated_empty_index_and_log(tmp_path: Path) -> None:
    from markdown_vault_mcp.okf import build_index_markdown, build_log_markdown

    (tmp_path / "index.md").write_text(build_index_markdown("Index", []))
    (tmp_path / "log.md").write_text(build_log_markdown([])[0])
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    assert result.report.verdict == "conformant"


def test_index_rejects_whitespace_only_link_title() -> None:
    assert _audit({"index.md": b"# Index\n- [   ](a.md)\n"}).verdict == "nonconformant"
