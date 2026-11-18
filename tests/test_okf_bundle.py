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
from markdown_vault_mcp.vault import Vault, VaultSettings

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
    col = Vault(
        source_dir=source_dir,
        settings=VaultSettings(read_only=False, attachment_extensions=["png"]),
    )
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


def test_bundle_reads_attachment_without_encoding(
    vault: Vault, config: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_path: str) -> None:
        raise AssertionError("encoded read used")

    monkeypatch.setattr(vault.reader, "read_attachment", fail)
    result = build_okf_bundle(vault, config, folder="")
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("pic.png") == b"\x89PNGDATA"


def test_inventory_membership_and_report(source_dir: Path) -> None:
    (source_dir / "typed.md").write_text("---\ntype: Note\nx-extra: yes\n---\ntext\n")
    (source_dir / "excluded.md").write_text("# excluded\n")
    (source_dir / ".hidden.md").write_text("# hidden\n")
    settings = VaultSettings(
        read_only=False,
        attachment_extensions=["png"],
        required_frontmatter=["title"],
        exclude_patterns=["excluded.md", ".hidden.md"],
    )
    config = ProjectConfig(source_dir=source_dir, attachment_extensions=("png",))
    vault = Vault(source_dir=source_dir, settings=settings)
    try:
        vault.index.build_index()
        result = build_okf_bundle(vault, config)
        members = _members(result.data)
        assert "typed.md" in members and "b.md" in members
        assert "excluded.md" not in members and ".hidden.md" not in members
        assert "typed.md" in result.report.local_policy.non_admitted
        assert "typed.md" not in {
            finding.path for finding in result.report.format_findings
        }
        assert "b.md" in {finding.path for finding in result.report.format_findings}
        assert result.report.omitted["index_excluded"] == 3
        assert result.report.omitted["hidden"] == 1
        assert result.report.omitted["conventions"] == 1
        assert result.report.omitted["templates"] == 1
        both = build_okf_bundle(
            vault, config, include_index_excluded=True, include_hidden=True
        )
        assert {"excluded.md", ".hidden.md"} <= _members(both.data)
        assert ".hidden.md" not in _members(
            build_okf_bundle(vault, config, include_hidden=True).data
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("typed.md") == (source_dir / "typed.md").read_bytes()
    finally:
        vault.close()


def test_large_note_and_symlink_are_reported(source_dir: Path, tmp_path: Path) -> None:
    (source_dir / "large.md").write_text("---\ntype: Note\n---\n" + "x" * 70000)
    outside = tmp_path / "outside.md"
    outside.write_text("private")
    (source_dir / "escape.md").symlink_to(outside)
    vault = Vault(
        source_dir=source_dir,
        settings=VaultSettings(read_only=False, attachment_extensions=["png"]),
    )
    try:
        vault.index.build_index()
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=source_dir, attachment_extensions=("png",))
        )
        assert "large.md" in _members(result.data)
        assert result.report.uninspected["large.md"] == "note_limit"
        assert "escape.md" not in _members(result.data)
        assert result.report.uninspected["escape.md"] == "symlink"
    finally:
        vault.close()


def test_bundle_preserves_bom_with_and_without_link_rewrite(
    source_dir: Path, vault: Vault, config: ProjectConfig
) -> None:
    untouched = b"\xef\xbb\xbf# BOM\n"
    rewritten = b"\xef\xbb\xbf# Link\nSee [[b]].\n"
    (source_dir / "bom.md").write_bytes(untouched)
    (source_dir / "linked.md").write_bytes(rewritten)
    vault.index.reindex()
    with zipfile.ZipFile(io.BytesIO(build_okf_bundle(vault, config).data)) as archive:
        assert archive.read("bom.md") == untouched
        assert archive.read("linked.md") == b"\xef\xbb\xbf# Link\nSee [b](/b.md).\n"


def test_inventory_failure_refuses_export(
    vault: Vault, config: ProjectConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleEntry, BundleInspection

    def unavailable(request):
        return BundleInspection(
            request,
            "inspected",
            "unavailable",
            (BundleEntry("deep", "directory", "unavailable", "depth_limit"),),
        )

    monkeypatch.setattr(vault.reader, "inspect_bundle_tree", unavailable)
    with pytest.raises(ValueError, match=r"deep.*depth_limit"):
        build_okf_bundle(vault, config)
    monkeypatch.setattr(
        vault.reader,
        "inspect_bundle_tree",
        lambda request: BundleInspection(request, "unavailable"),
    )
    with pytest.raises(ValueError, match=r"guides.*unavailable"):
        build_okf_bundle(vault, config, folder="guides")


def test_scope_normalization_and_validation(
    vault: Vault, config: ProjectConfig
) -> None:
    for scope in ("guides/./", "guides//"):
        assert _members(build_okf_bundle(vault, config, folder=scope).data) == {
            "guides/one.md"
        }
    for scope in ("/guides", "guides/../guides", "guides\\\\x", "guides/\x00"):
        with pytest.raises(ValueError):
            build_okf_bundle(vault, config, folder=scope)


def test_raw_links_for_notes_outside_graph(source_dir: Path) -> None:
    body = "---\ntype: Note\n---\n[[target]]\n"
    (source_dir / "target.md").write_text("---\ntitle: Target\n---\n")
    (source_dir / "nonadmitted.md").write_text(body)
    (source_dir / "excluded.md").write_text(body)
    vault = Vault(
        source_dir=source_dir,
        settings=VaultSettings(
            read_only=False,
            required_frontmatter=["title"],
            exclude_patterns=["excluded.md"],
        ),
    )
    try:
        vault.index.build_index()
        (source_dir / "new.md").write_text(body)
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=source_dir), include_index_excluded=True
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            for path in ("nonadmitted.md", "excluded.md", "new.md"):
                assert archive.read(path) == body.encode()
    finally:
        vault.close()


def test_export_pages_and_read_failure(
    source_dir: Path,
    vault: Vault,
    config: ProjectConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from markdown_vault_mcp import okf_bundle

    for i in range(1001):
        (source_dir / f"page-{i:04}.md").write_text("# Page\n")
    original = okf_bundle._read_safe

    def read(source, path):
        if path == "page-0500.md":
            raise OSError("refused")
        return original(source, path)

    monkeypatch.setattr(okf_bundle, "_read_safe", read)
    result = build_okf_bundle(vault, config)
    members = _members(result.data)
    assert "page-0000.md" in members and "page-1000.md" in members
    assert "page-0500.md" not in members
    assert result.report.uninspected["page-0500.md"] == "read_unavailable"


def test_deep_inventory_and_unsupported_nofollow_refuse(
    source_dir: Path,
    vault: Vault,
    config: ProjectConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import markdown_vault_mcp.utils.fs as fs

    directory = source_dir
    for _ in range(32):
        directory = directory / "d"
        directory.mkdir()
    (directory / "deep.md").write_text("# Deep\n")
    with pytest.raises(ValueError, match="depth_limit"):
        build_okf_bundle(vault, config)
    monkeypatch.setattr(fs, "_NOFOLLOW_SUPPORTED", False)
    with pytest.raises(ValueError, match=r"inventory unavailable.*unavailable"):
        build_okf_bundle(vault, config)
