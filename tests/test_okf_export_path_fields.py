"""Path-valued frontmatter relocates without reserializing the document."""

import io
import zipfile

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.okf_bundle import build_okf_bundle
from markdown_vault_mcp.vault import Vault, VaultSettings


def export(tmp_path, text, **kwargs):
    (tmp_path / "guides").mkdir(exist_ok=True)
    (tmp_path / "guides/a.md").write_text(text)
    (tmp_path / "guides/b.md").write_text("# B\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides", **kwargs
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            return archive.read("a.md").decode(), result.report
    finally:
        vault.close()


def test_fields_preserve_yaml_and_source(tmp_path):
    text = """---
# keep comment
resource: '/guides/b.md' # keep
computation: /guides/b.md
executor: {resource: "/guides/b.md", extra: 42}
attester:
  resource: ./b.md
sources:
  - resource: /guides/b.md
  - resource: https://example.org/x
  - resource: {scope: references/}
unknown: /guides/b.md
---
Body [link](/guides/b.md)
"""
    output, report = export(tmp_path, text)
    expected = (
        text.replace("resource: '/guides/b.md'", "resource: '/b.md'")
        .replace("computation: /guides/b.md", "computation: /b.md")
        .replace('resource: "/guides/b.md"', 'resource: "/b.md"')
        .replace("- resource: /guides/b.md", "- resource: /b.md")
        .replace("[link](/guides/b.md)", "[link](/b.md)")
    )
    assert output == expected
    assert report.transformed == ["a.md"]
    assert (tmp_path / "guides/a.md").read_text() == text


def test_ambiguous_and_outside(tmp_path):
    text = "---\nresource: references/b.md\nsources:\n  - resource: /outside.md\n---\n"
    output, report = export(tmp_path, text)
    assert output == text
    assert [(x.path, x.target) for x in report.ambiguous_path] == [
        ("a.md", "references/b.md")
    ]
    assert [(x.path, x.target) for x in report.dependencies] == [
        ("a.md", "/outside.md")
    ]


def test_collision_or_external_base(tmp_path):
    text = "---\nresource: /b.md\n---\n"
    with pytest.raises(ValueError, match="collision"):
        export(tmp_path, text)
    output, report = export(tmp_path, text, external_base="https://example.org/vault")
    assert "resource: https://example.org/vault/b.md" in output
    assert report.dependencies[0].target == "/b.md"
