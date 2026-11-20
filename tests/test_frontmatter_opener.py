"""Frontmatter boundaries agree across parsing and body extraction."""

from pathlib import Path

import pytest

from markdown_vault_mcp.bundle_inventory import BundleInspectionRequest
from markdown_vault_mcp.scanner import parse_frontmatter, strip_frontmatter_block
from markdown_vault_mcp.vault import Vault


@pytest.mark.parametrize(
    ("text", "metadata", "body"),
    [
        ("---\ntype: Metric\n---\nBody", {"type": "Metric"}, "Body"),
        ("\ufeff---\ntype: Metric\n---\nBody", {"type": "Metric"}, "Body"),
        ("---\r\ntype: Metric\r\n---\r\nBody", {"type": "Metric"}, "Body"),
        ("\n---\ntype: Metric\n---\nBody", {}, None),
        (" ---\ntype: Metric\n---\nBody", {}, None),
        ("----\ntype: Metric\n----\nBody", {}, None),
        ("--- \ntype: Metric\n---\nBody", {}, None),
        ("---\ntype: Metric\n--- \nBody", {}, None),
        ("---\ntype: Metric\n----\nBody", {}, None),
        ("---\ndescription: |\n  --- \n---\nBody", {"description": "--- \n"}, "Body"),
    ],
)
def test_yaml_boundaries(text: str, metadata: dict[str, str], body: str | None) -> None:
    assert parse_frontmatter(text).metadata == metadata
    assert strip_frontmatter_block(text) == (text if body is None else body)


def test_public_inventory_rejects_leading_blank_line(tmp_path: Path) -> None:
    (tmp_path / "metric.md").write_text("\n---\ntype: Metric\n---\n")
    vault = Vault(source_dir=tmp_path)
    try:
        result = vault.reader.inspect_bundle(BundleInspectionRequest(root=""))
        assert result.entries[0].is_concept is False
    finally:
        vault.close()


def test_json_handler_remains_supported() -> None:
    text = '{\n"type": "Metric"\n}\nBody'
    assert parse_frontmatter(text).metadata == {"type": "Metric"}
    assert strip_frontmatter_block(text) == "Body"
