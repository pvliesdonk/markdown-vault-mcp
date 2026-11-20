"""Frontmatter boundaries agree across parsing and body extraction."""

from pathlib import Path

import pytest

from markdown_vault_mcp.bundle_inventory import BundleInspectionRequest
from markdown_vault_mcp.scanner import parse_frontmatter, strip_frontmatter_block
from markdown_vault_mcp.vault import Vault, VaultSettings


@pytest.mark.parametrize(
    ("text", "metadata", "body"),
    [
        ("---\ntype: Metric\n---\nBody", {"type": "Metric"}, "Body"),
        ("---\ntype: Metric\n---", {"type": "Metric"}, ""),
        ("---\ntype: Metric\n--- ", {}, None),
        # Public string callers can supply a BOM without file decoding.
        ("\ufeff---\ntype: Metric\n---\nBody", {"type": "Metric"}, "Body"),
        ("---\r\ntype: Metric\r\n---\r\nBody", {"type": "Metric"}, "Body"),
        ("\n---\ntype: Metric\n---\nBody", {}, None),
        (" ---\ntype: Metric\n---\nBody", {}, None),
        ("----\ntype: Metric\n----\nBody", {}, None),
        ("--- \ntype: Metric\n---\nBody", {}, None),
        ("---\ntype: Metric\n--- \nBody", {}, None),
        ("---\ntype: Metric\n----\nBody", {}, None),
    ],
)
def test_yaml_boundaries(text: str, metadata: dict[str, str], body: str | None) -> None:
    assert parse_frontmatter(text).metadata == metadata
    assert strip_frontmatter_block(text) == (text if body is None else body)


@pytest.mark.parametrize(
    "text",
    [
        "\n---\ntype: Metric\n---\n",
        "\n---\ntype: [\n---\n",
        "\n---\ntype: *alias\n---\n",
    ],
)
def test_public_inventory_uses_frontmatter_boundaries(
    tmp_path: Path, text: str
) -> None:
    (tmp_path / "metric.md").write_text(text)
    vault = Vault(source_dir=tmp_path)
    try:
        result = vault.reader.inspect_bundle(BundleInspectionRequest(root=""))
        entry = result.entries[0]
        assert entry.state == "inspected"
        assert entry.parse_state == "valid"
        assert entry.reason is None
        assert not entry.is_concept
    finally:
        vault.close()


@pytest.mark.parametrize("operation", ["read", "index"])
def test_json_header_only_with_trailing_space_remains_readable(
    tmp_path: Path, operation: str
) -> None:
    text = '{\n"title": "Kept"\n}  '
    (tmp_path / "note.md").write_text(text)
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        if operation == "read":
            assert strip_frontmatter_block(text) == text
            note = vault.reader.read("note.md")
            assert note is not None
            assert note.frontmatter.get("title") == "Kept"
        else:
            vault.index.build_index()
            assert "note.md" in {note.path for note in vault.reader.list_documents()}
    finally:
        vault.close()


def test_invalid_yaml_date_raises_normalized_error() -> None:
    from markdown_vault_mcp.scanner import MalformedFrontmatterError

    with pytest.raises(MalformedFrontmatterError):
        parse_frontmatter("---\ndate: 2026-13-45\n---\nBody")


@pytest.mark.parametrize("leading_blank", [False, True])
def test_crlf_body_matches_lf_in_public_index(
    tmp_path: Path, leading_blank: bool
) -> None:
    prefix = "\n" if leading_blank else ""
    text = prefix + "---\ntitle: Test\n---\n# Header\nneedle content\nsecond line\n"
    crlf = text.replace("\n", "\r\n")
    expected_raw = (
        crlf if leading_blank else "# Header\r\nneedle content\r\nsecond line\r\n"
    )
    assert strip_frontmatter_block(crlf) == expected_raw
    (tmp_path / "lf.md").write_bytes(text.encode())
    (tmp_path / "crlf.md").write_bytes(crlf.encode())
    vault = Vault(source_dir=tmp_path)
    try:
        vault.index.build_index()
        notes = {note.path: note for note in vault.reader.list_documents()}
        assert notes["lf.md"].content_chars == notes["crlf.md"].content_chars
        results = {
            result.path: result
            for result in vault.reader.search("needle", mode="keyword", snippet_words=0)
        }
        assert (
            results["lf.md"].sections[0].content
            == results["crlf.md"].sections[0].content
        )
        assert "\r" not in results["crlf.md"].sections[0].content
    finally:
        vault.close()
