"""Subtree exports relocate body links without changing source bytes."""

import io
import zipfile

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.okf_bundle import build_okf_bundle
from markdown_vault_mcp.vault import Vault, VaultSettings


def test_subtree_relocates_and_reports(tmp_path):
    (tmp_path / "guides").mkdir()
    original = b"---\ntype: Note\nresource: /guides/b.md\n---\n[a](/guides/b.md?x=1#h) [b](b.md) [c](/other.md)\n"
    (tmp_path / "guides/a.md").write_bytes(original)
    (tmp_path / "guides/b.md").write_text("# B\n")
    (tmp_path / "index.md").write_text('---\nokf_version: "0.2"\n---\n')
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert set(archive.namelist()) == {"a.md", "b.md"}
            assert archive.read("a.md") == original.replace(
                b"/guides/b.md?x=1#h", b"/b.md?x=1#h"
            )
        assert result.report.transformed == ["guides/a.md"]
        assert result.report.dependencies[0].target == "/other.md"
        assert (tmp_path / "guides/a.md").read_bytes() == original
    finally:
        vault.close()


def test_collision_refused_or_externalized(tmp_path):
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/a.md").write_text("[x](/b.md#part)")
    (tmp_path / "guides/b.md").write_text("# B")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        config = ProjectConfig(source_dir=tmp_path)
        with pytest.raises(ValueError, match="collision"):
            build_okf_bundle(vault, config, folder="guides")
        result = build_okf_bundle(
            vault, config, folder="guides", external_base="https://example.org/vault/"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("a.md") == b"[x](https://example.org/vault/b.md#part)"
    finally:
        vault.close()


def test_reference_image_and_code(tmp_path):
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/a.md").write_text(
        "[ref]: /guides/b.md\n\n![image](/guides/b.md) `[code](/guides/b.md)`\n"
    )
    (tmp_path / "guides/b.md").write_text("# B")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert (
                archive.read("a.md")
                == b"[ref]: /b.md\n\n![image](/b.md) `[code](/guides/b.md)`\n"
            )
    finally:
        vault.close()
