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


@pytest.mark.parametrize(
    ("body", "verdict"),
    [
        (b"# Index\n* broken\n", "nonconformant"),
        (b"# Index\n* [](a.md)\n", "nonconformant"),
        (b"# Index\n- [   ](a.md)\n", "nonconformant"),
        (b"# Index\n-\n", "nonconformant"),
        (b"# Index\n* [A](a.md)\n\n  prose\n", "nonconformant"),
        ("# Index\n* [A](a.md) — desc\n".encode(), "nonconformant"),
        (b"# Index\n- [A](a.md)\n  - [B](b.md)\n", "nonconformant"),
        (b"# Vault\n\nWelcome to the vault.\n\n- [Note](note.md)\n", "conformant"),
        (b"# Index\nPlain prose\n", "conformant"),
        (b"# Index\n\n---\n", "conformant"),
        (b"# Index\n\n> - [A](a.md)\n", "conformant"),
        (b"# Index\n```\n* [A](a.md)\n```\n", "conformant"),
    ],
)
def test_index_body_checks_structure_and_ignores_fenced_examples(
    body: bytes, verdict: str
) -> None:
    from markdown_vault_mcp.okf import build_index_markdown

    valid = build_index_markdown("Index", [("A", "/a.md", "A note")])
    assert _audit({"index.md": valid.encode()}).verdict == "conformant"
    assert _audit({"index.md": body}).verdict == verdict


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
    ],
)
def test_format_rules(name: str, data: bytes, verdict: str, reason: str | None) -> None:
    report = _audit({name: data})
    assert report.verdict == verdict
    assert reason is None or any(f.reason == reason for f in report.format_findings)


def test_advisories_and_broken_links_do_not_change_verdict() -> None:
    report = _audit(
        {
            "note.md": b"---\ntype: Unknown\n---\n[missing](missing.md)",
            "other.unknown": b"anything",
        }
    )
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
        ("log.md", ["log_date_headings", "log_entry_shape"]),
        ("note.md", ["yaml_frontmatter", "type"]),
    ],
)
def test_unreadable_reserved_file_names_its_checks(
    name: str, checks: list[str]
) -> None:
    report = _audit({name: b"\xff"})
    assert report.verdict == "incomplete"
    assert report.unchecked[name] == checks
    assert report.format_findings == []


@pytest.mark.parametrize(
    ("name", "body", "reasons"),
    [
        ("index.md", b"# Index\n", ["index_frontmatter"]),
        ("sub/index.md", b"# Index\n", ["index_frontmatter"]),
        (
            "index.md",
            b"# Index\n* broken\n",
            ["index_frontmatter", "index_entry_shape"],
        ),
    ],
)
def test_unparseable_index_still_checks_body(
    name: str, body: bytes, reasons: list[str]
) -> None:
    report = _audit({name: b"---\ninvalid: [\n---\n" + body})
    assert report.verdict == "nonconformant"
    assert report.unchecked == {}
    assert [f.reason for f in report.format_findings] == reasons


def test_malformed_frontmatter_still_reports_body_links() -> None:
    report = _audit({"note.md": b"---\ntype: [\n---\n[missing](missing.md)\n"})
    assert [(item.path, item.target) for item in report.broken_links] == [
        ("note.md", "missing.md")
    ]


@pytest.mark.parametrize("missing", ["title", "description", "resource", "tags"])
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


def test_artifact_observer_masks_code_and_finds_wiki_attachments() -> None:
    body = b"---\ntype: T\n---\n![[missing.png]]\n\n    [[code]]\n\n`[[inline]]`\n\n```\n[[fenced]]\n```\n"
    report = _audit({"note.md": body})
    assert [(item.path, item.target) for item in report.broken_links] == [
        ("note.md", "missing.png")
    ]


def test_archive_directories_and_wiki_name_are_present() -> None:
    body = b"---\ntype: T\n---\n[root](/) [dot](.) [here](./) [[b]] [file](target.md/) [encoded](target.md%2F) [directory](dir/) [plain](target.md)\n"
    report = _audit(
        {
            "note.md": body,
            "sub/b.md": b"---\ntype: T\n---\n",
            "dir/note.md": b"---\ntype: T\n---\n",
            "target.md": b"---\ntype: T\n---\n",
        }
    )
    assert [item.target for item in report.broken_links] == [
        "target.md/",
        "target.md%2F",
    ]


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


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"# Log\n",
        b"# Log\n## 2026-02-28\n",
        b"# Log\n## 2026-01-01\n## 2026-01-01\n## 2025-01-01\n",
        b"# Log\n## 2026-01-01\n- Entry\n\n  More prose.\n\n  ```\n  ## sample\n  ```\n",
    ],
)
def test_log_allows_entry_content_after_date_and_title_only(body: bytes) -> None:
    assert _audit({"log.md": body}).verdict == "conformant"


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


@pytest.mark.parametrize("header", ["type: T\nhandler: extra", "type: T\n42: value"])
def test_unusual_yaml_keys_export_without_crashing(tmp_path: Path, header: str) -> None:
    (tmp_path / "note.md").write_text(f"---\n{header}\n---\n# Note\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    assert result.report.verdict == "conformant"
    assert result.data


def test_artifact_relative_traversal_preserves_depth_and_ties() -> None:
    report = _audit(
        {
            "note.md": b"---\ntype: T\n---\n[a](../target.md) [b](%2e%2e/target.md) [[../target]]\n",
            "a/note.md": b"---\ntype: T\n---\n[inside](../target.md) [escape](../../target.md) [[../../target]] [[choice]]\n",
            "target.md": b"---\ntype: T\n---\n",
            "a/choice.md": b"---\ntype: T\n---\n",
            "b/choice.md": b"---\ntype: T\n---\n",
        }
    )
    assert [(item.path, item.target) for item in report.broken_links] == [
        ("a/note.md", "../../target.md"),
        ("a/note.md", "../../target"),
        ("note.md", "../target.md"),
        ("note.md", "%2e%2e/target.md"),
        ("note.md", "../target"),
    ]


@pytest.mark.parametrize("header", ["handler: extra", "42: value"])
def test_unusual_root_index_key_is_a_format_finding(header: str) -> None:
    report = _audit({"index.md": f"---\n{header}\n---\n# Index\n".encode()})
    assert report.verdict == "nonconformant"
    assert [item.reason for item in report.format_findings] == ["index_frontmatter"]


@pytest.mark.parametrize(
    "body",
    [
        b"## 2026-01-01\nprose\n",
        b"## 2026-01-01\n### Heading\n",
        b"## 2026-01-01\n- item\n  - nested\n",
        b"## 2026-01-01\n1. item\n",
        b"- early\n",
        b"# Log\nUngrouped entry\n",
        b"# Log\n```\n## example\n```\n",
        b"# Log\n## Yesterday\n",
        b"# Log\n## 2026-02-30\n",
        b"# Log\n## 2025-01-01\n- Old\n## 2026-01-01\n- New\n",
    ],
)
def test_log_rejects_nonflat_entries(body: bytes) -> None:
    assert _audit({"log.md": body}).verdict == "nonconformant"


def test_encoded_colon_is_a_local_artifact_path() -> None:
    note = b"---\ntype: T\n---\n[missing](manual%3A2026.pdf) [web](https://example.com) [escaped](https\\://example.com) [entity](https&#58;//example.com) [pointy](<https://example.com>)\n"
    report = _audit({"note.md": note})
    assert [item.target for item in report.broken_links] == ["manual%3A2026.pdf"]
    assert _audit({"note.md": note, "manual:2026.pdf": b"present"}).broken_links == []


def test_resource_failure_marks_member_unchecked_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from markdown_vault_mcp import okf_export_conformance as module

    original = module._inspect

    def inspect(path, raw, report, links):
        if path == "bad.md":
            raise MemoryError
        original(path, raw, report, links)

    monkeypatch.setattr(module, "_inspect", inspect)
    report = _audit({"bad.md": b"", "other.md": b"missing"})
    assert report.verdict == "incomplete"
    assert report.unchecked["bad.md"] == ["yaml_frontmatter", "type"]
    assert any(f.path == "other.md" for f in report.format_findings)


@pytest.mark.parametrize(
    ("name", "checks"),
    [
        ("index.md", ["index_frontmatter", "index_entry_shape"]),
        ("log.md", ["log_date_headings", "log_entry_shape"]),
        ("note.md", ["yaml_frontmatter", "type"]),
    ],
)
@pytest.mark.parametrize("error", [OverflowError, MemoryError, RecursionError])
def test_resource_failures_name_role_checks(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    checks: list[str],
    error: type[Exception],
) -> None:
    from markdown_vault_mcp import okf_export_conformance as module

    def fail(*_args: object) -> None:
        raise error

    monkeypatch.setattr(module, "_inspect", fail)
    report = _audit({name: b""})
    assert report.verdict == "incomplete"
    assert report.unchecked[name] == checks


def test_deep_yaml_export_is_incomplete(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text(
        "---\ntype: T\ndeep: " + "[" * 30000 + "]" * 30000 + "\n---\n"
    )
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    assert result.data
    assert result.report.verdict == "incomplete"
    assert result.report.unchecked["note.md"] == ["yaml_frontmatter", "type"]


@pytest.mark.parametrize("folder", ["", "sub"])
@pytest.mark.parametrize("tag", ["!!bool maybe", "!!timestamp x"])
def test_invalid_yaml_tag_export_is_nonconformant(
    tmp_path: Path, folder: str, tag: str
) -> None:
    root = tmp_path / folder if folder else tmp_path
    if folder:
        root.mkdir()
    (root / "note.md").write_text(f"---\ntype: T\nx: {tag}\n---\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder=folder
        )
    finally:
        vault.close()
    assert result.report.verdict == "nonconformant"
    assert [(f.path, f.reason) for f in result.report.format_findings] == [
        ("note.md", "malformed_frontmatter")
    ]
