"""Tests for build_okf_bundle — the OKF bundle-export builder (#963).

Exercises the pure builder against a small writable vault: what is included and
excluded, wikilink rewriting, frontmatter/non-conformant passthrough, folder
scoping, and byte reproducibility. The builder never mutates the vault.
"""

from __future__ import annotations

import io
import zipfile
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.okf_bundle import build_okf_bundle
from markdown_vault_mcp.vault import Vault

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "index.md").write_text('---\nokf_version: "0.2"\n---\n# Index\n')
    (root / "log.md").write_text("# Log\n")
    # Conformant note with a resolvable and an unresolvable wikilink.
    (root / "a.md").write_text(
        "---\ntype: Note\ntags: [x]\n---\n# A\nSee [[b]] and [[ghost]].\n"
    )
    (root / "b.md").write_text("# B\n")  # non-conformant (no type) — kept as-is
    (root / "_conventions.md").write_text("# conventions\n")
    (root / "pic.png").write_bytes(b"\x89PNGDATA")
    guides = root / "guides"
    guides.mkdir()
    (guides / "one.md").write_text("# One\n")
    templates = root / "_templates"
    templates.mkdir()
    (templates / "t.md").write_text("# template\n")
    return root


@pytest.fixture
def config(source_dir: Path) -> ProjectConfig:
    return ProjectConfig(
        source_dir=source_dir, read_only=False, attachment_extensions=("png",)
    )


@pytest.fixture
def vault(source_dir: Path) -> Iterator[Vault]:
    col = Vault(source_dir=source_dir, read_only=False, attachment_extensions=["png"])
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


def _members(data: bytes) -> set[str]:
    return set(zipfile.ZipFile(io.BytesIO(data)).namelist())


def test_bundle_includes_notes_and_attachments(vault: Vault, config: ProjectConfig):
    result = build_okf_bundle(vault, config, folder="")
    members = _members(result.data)
    # Notes (incl. reserved navigation files) and the attachment are present.
    assert {"index.md", "log.md", "a.md", "b.md", "guides/one.md", "pic.png"} <= members
    assert result.attachments == 1
    assert result.notes >= 5


def test_bundle_excludes_convention_and_template_files(
    vault: Vault, config: ProjectConfig
):
    members = _members(build_okf_bundle(vault, config, folder="").data)
    assert "_conventions.md" not in members
    assert "_templates/t.md" not in members
    assert not any(m.startswith("_templates/") for m in members)


def test_bundle_keeps_reserved_index_and_log(vault: Vault, config: ProjectConfig):
    members = _members(build_okf_bundle(vault, config, folder="").data)
    assert "index.md" in members
    assert "log.md" in members


def test_bundle_rewrites_resolvable_wikilinks(vault: Vault, config: ProjectConfig):
    zf = zipfile.ZipFile(io.BytesIO(build_okf_bundle(vault, config, folder="").data))
    body = zf.read("a.md").decode("utf-8")
    assert "[b](/b.md)" in body  # resolvable → root-absolute markdown link
    assert "[[b]]" not in body
    assert "[[ghost]]" in body  # unresolvable left untouched


def test_bundle_preserves_frontmatter_and_nonconformant(
    vault: Vault, config: ProjectConfig
):
    zf = zipfile.ZipFile(io.BytesIO(build_okf_bundle(vault, config, folder="").data))
    a = zf.read("a.md").decode("utf-8")
    assert a.startswith("---\ntype: Note")  # frontmatter intact
    # A non-conformant note (no OKF type) is included verbatim, not dropped.
    assert zf.read("b.md").decode("utf-8") == "# B\n"


def test_bundle_folder_scope(vault: Vault, config: ProjectConfig):
    members = _members(build_okf_bundle(vault, config, folder="guides").data)
    assert members == {"guides/one.md"}


def test_bundle_is_byte_reproducible(vault: Vault, config: ProjectConfig):
    first = build_okf_bundle(vault, config, folder="").data
    second = build_okf_bundle(vault, config, folder="").data
    assert first == second


def test_is_excluded_matches_convention_and_template_files():
    from markdown_vault_mcp.okf_bundle import _is_excluded

    assert _is_excluded("_conventions.md", "_conventions.md", "_templates")
    assert _is_excluded("sub/_conventions.md", "_conventions.md", "_templates")
    assert _is_excluded("_templates", "_conventions.md", "_templates")
    assert _is_excluded("_templates/t.md", "_conventions.md", "_templates")
    assert not _is_excluded("a.md", "_conventions.md", "_templates")
    assert not _is_excluded("index.md", "_conventions.md", "_templates")


def test_safe_abs_rejects_escape(tmp_path: Path):
    from markdown_vault_mcp.okf_bundle import _safe_abs

    with pytest.raises(ValueError, match=r"escapes"):
        _safe_abs(tmp_path, "../outside.md")
