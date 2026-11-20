"""Tests for build_okf_bundle — the OKF bundle-export builder (#963).

Exercises the pure builder against a small writable vault: what is included and
excluded, wikilink rewriting, frontmatter/non-conformant passthrough, folder
scoping, and byte reproducibility. The builder never mutates the vault.
"""

from __future__ import annotations

import io
import os
import shutil
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
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("pic.png") == b"\x89PNGDATA"
        assert archive.read("b.md") == b"# B\n"
    assert result.notes == 5


def test_whole_vault_keeps_inherited_wikilink_conversion(tmp_path):
    body = "[[b]]\n\n```\n[[b]]\n```\n"
    (tmp_path / "a.md").write_text(body)
    (tmp_path / "b.md").write_text("# B\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        vault.index.build_index()
        result = build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("a.md") == b"[b](/b.md)\n\n```\n[b](/b.md)\n```\n"
    finally:
        vault.close()


def test_bundle_rewrites_resolvable_wikilinks(vault: Vault, config: ProjectConfig):
    zf = zipfile.ZipFile(io.BytesIO(build_okf_bundle(vault, config, folder="").data))
    body = zf.read("a.md").decode("utf-8")
    assert "[b](/b.md)" in body  # resolvable → root-absolute markdown link
    assert "[[b]]" not in body
    assert "[[ghost]]" in body  # unresolvable left untouched


@pytest.mark.parametrize("scope", ["guides", "guides/./", "guides//"])
def test_bundle_folder_scope(vault: Vault, config: ProjectConfig, scope: str):
    members = _members(build_okf_bundle(vault, config, folder=scope).data)
    assert members == {"one.md"}


def test_bundle_is_byte_reproducible(vault: Vault, config: ProjectConfig):
    first = build_okf_bundle(vault, config, folder="").data
    second = build_okf_bundle(vault, config, folder="").data
    assert first == second


def test_inventory_membership_and_report(source_dir: Path) -> None:
    (source_dir / "typed.md").write_text("---\ntype: Note\nx-extra: yes\n---\ntext\n")
    (source_dir / "excluded.md").write_text("# excluded\n")
    (source_dir / ".hidden.md").write_text("# hidden\n")
    settings = VaultSettings(
        read_only=False,
        attachment_extensions=["png"],
        required_frontmatter=["title"],
        exclude_patterns=["excluded.md", ".hidden.md", "_conventions.md"],
    )
    config = ProjectConfig(source_dir=source_dir, attachment_extensions=("png",))
    vault = Vault(source_dir=source_dir, settings=settings)
    try:
        vault.index.build_index()
        result = build_okf_bundle(vault, config)
        members = _members(result.data)
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert (
                archive.read("typed.md")
                == b"---\ntype: Note\nx-extra: yes\n---\ntext\n"
            )
        assert "typed.md" in members and "b.md" in members
        assert "excluded.md" not in members and ".hidden.md" not in members
        assert "_conventions.md" not in members and "_templates/t.md" not in members
        assert "typed.md" in result.report.local_policy.non_admitted
        assert "typed.md" not in {
            finding.path for finding in result.report.format_findings
        }
        assert "b.md" in {finding.path for finding in result.report.format_findings}
        assert result.report.omitted["index_excluded"] == 3
        assert result.report.omitted["not_part_of_bundle"] == 1
        assert result.report.omitted["conventions"] == 1
        assert result.report.omitted["templates"] == 1
        excluded_only = _members(
            build_okf_bundle(vault, config, include_index_excluded=True).data
        )
        assert ".hidden.md" not in excluded_only and "excluded.md" in excluded_only
    finally:
        vault.close()


def test_large_note_is_reported(source_dir: Path) -> None:
    (source_dir / "large.md").write_text("---\ntype: Note\n---\n" + "x" * 70000)
    vault = Vault(source_dir=source_dir, settings=VaultSettings())
    try:
        result = build_okf_bundle(vault, ProjectConfig(source_dir=source_dir))
        assert "large.md" in _members(result.data)
        assert result.report.uninspected["large.md"] == "note_limit"
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


def test_local_policy_unknown_and_stale_index_bytes(source_dir: Path) -> None:
    body = "---\ntype: Note\n---\n[[target]]\n"
    (source_dir / "target.md").write_text("---\ntitle: Target\n---\n")
    (source_dir / "nonadmitted.md").write_text(body)
    (source_dir / "excluded.md").write_text(body)
    (source_dir / "invalid.md").write_bytes(b"\xff")
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
        (source_dir / "target.md").write_text(body)
        (source_dir / "new.md").write_text(body)
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=source_dir), include_index_excluded=True
        )
        policy = result.report.local_policy
        assert policy.basis == "last_observed_index"
        assert "nonadmitted.md" in policy.non_admitted
        assert {"excluded.md", "new.md", "invalid.md"} <= set(policy.unknown)
        assert "target.md" not in policy.non_admitted + policy.unknown
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("target.md") == body.encode()
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
    result = build_okf_bundle(vault, config)
    members = _members(result.data)
    assert "page-0000.md" in members and "page-1000.md" in members
    outside = source_dir.parent / "outside-secret"
    outside.write_bytes(b"secret")

    def swap_then_read(source, path):
        if path == "page-0500.md":
            target = source_dir / path
            target.unlink()
            target.symlink_to(outside)
        return original(source, path)

    monkeypatch.setattr(okf_bundle, "_read_safe", swap_then_read)
    with pytest.raises(OSError):
        build_okf_bundle(vault, config)
    assert outside.read_bytes() == b"secret"


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
    with pytest.raises(ValueError, match=r"guides.*unavailable"):
        build_okf_bundle(vault, config, folder="guides")


def test_cold_index_local_policy_is_unknown(source_dir: Path) -> None:
    vault = Vault(source_dir=source_dir, settings=VaultSettings(read_only=False))
    try:
        result = build_okf_bundle(vault, ProjectConfig(source_dir=source_dir))
        assert {"a.md", "b.md", "guides/one.md"} <= set(
            result.report.local_policy.unknown
        )
    finally:
        vault.close()


@pytest.mark.parametrize("error_number", [5, 2], ids=["io-error", "vanished"])
@pytest.mark.parametrize("name", ["selected.md", "selected.png"])
def test_inventory_selected_read_failure_refuses(
    source_dir: Path, monkeypatch: pytest.MonkeyPatch, error_number: int, name: str
) -> None:
    from markdown_vault_mcp import bundle_inventory

    (source_dir / name).write_bytes(b"content")
    original = bundle_inventory.read_regular_nofollow

    def fail(parent, filename, limit):
        if filename == name:
            raise OSError(error_number, "failed")
        return original(parent, filename, limit)

    monkeypatch.setattr(bundle_inventory, "read_regular_nofollow", fail)
    vault = Vault(
        source_dir=source_dir, settings=VaultSettings(attachment_extensions=["png"])
    )
    try:
        with pytest.raises(OSError, match=name.replace(".", r"\.") + ": io_error"):
            build_okf_bundle(
                vault,
                ProjectConfig(source_dir=source_dir, attachment_extensions=("png",)),
            )
    finally:
        vault.close()


def test_omitted_hidden_inventory_failure(
    source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp import bundle_inventory

    (source_dir / ".hidden.md").write_text("hidden")
    original = bundle_inventory.read_regular_nofollow

    def fail(parent, filename, limit):
        if filename == ".hidden.md":
            raise OSError(5, "failed")
        return original(parent, filename, limit)

    monkeypatch.setattr(bundle_inventory, "read_regular_nofollow", fail)
    vault = Vault(source_dir=source_dir, settings=VaultSettings())
    try:
        result = build_okf_bundle(vault, ProjectConfig(source_dir=source_dir))
        assert result.report.omitted["not_part_of_bundle"] == 1

    finally:
        vault.close()


def test_selected_parser_failure_refuses(
    source_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from markdown_vault_mcp import bundle_inventory

    monkeypatch.setattr(
        bundle_inventory,
        "_metadata_too_complex",
        lambda _: (_ for _ in ()).throw(RuntimeError("failure")),
    )
    vault = Vault(source_dir=source_dir, settings=VaultSettings())
    try:
        with pytest.raises(OSError, match="unexpected_parser_error"):
            build_okf_bundle(vault, ProjectConfig(source_dir=source_dir))
    finally:
        vault.close()


@pytest.mark.parametrize("name", ["index.md", "log.md"])
@pytest.mark.parametrize(
    "options",
    [
        {},
        {"include_index_excluded": True},
    ],
)
def test_reserved_conventions_omitted_with_options(
    source_dir: Path, name: str, options: dict[str, bool]
) -> None:
    (source_dir / "guides" / name).write_text("conventions")
    config = ProjectConfig(source_dir=source_dir, conventions_file=name)
    vault = Vault(
        source_dir=source_dir,
        settings=VaultSettings(
            conventions_file=name, exclude_patterns=[name, f"guides/{name}"]
        ),
    )
    try:
        result = build_okf_bundle(vault, config, **options)
        assert name not in _members(result.data)
        assert f"guides/{name}" not in _members(result.data)
        assert result.report.omitted["conventions"] == 2
    finally:
        vault.close()


@pytest.mark.parametrize("excluded", [False, True])
@pytest.mark.parametrize("include_index_excluded", [False, True])
def test_hidden_symlink_selection(
    source_dir: Path, tmp_path: Path, excluded: bool, include_index_excluded: bool
) -> None:
    bin_dir = source_dir / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python3.14").symlink_to(tmp_path / "missing")
    vault = Vault(
        source_dir=source_dir,
        settings=VaultSettings(exclude_patterns=[".venv/**"] if excluded else []),
    )
    try:
        result = build_okf_bundle(
            vault,
            ProjectConfig(source_dir=source_dir),
            include_index_excluded=include_index_excluded,
        )
        assert "a.md" in _members(result.data)
        assert ".venv/bin/python3.14" not in _members(result.data)
        assert result.report.omitted["not_part_of_bundle"] == 1
    finally:
        vault.close()


@pytest.mark.parametrize(
    "kind",
    [
        "symlink",
        "hidden_symlink",
        "nonregular",
        "directory_symlink",
    ],
)
def test_selected_unavailable_entry_refuses(
    source_dir: Path, tmp_path: Path, kind: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret")
    if kind == "nonregular":
        path = source_dir / "x.md"
        os.mkfifo(path)
    elif kind == "directory_symlink":
        path = source_dir / "alias"
        path.symlink_to(outside, target_is_directory=True)
    else:
        path = source_dir / ("escape.md" if kind == "symlink" else "data.bak")
        path.symlink_to(outside / "secret.md")
    vault = Vault(source_dir=source_dir, settings=VaultSettings())
    try:
        with pytest.raises(OSError, match=path.name + ":"):
            build_okf_bundle(vault, ProjectConfig(source_dir=source_dir))
    finally:
        vault.close()


@pytest.mark.parametrize("bom,newline", [(b"", b"\n"), (b"\xef\xbb\xbf", b"\r\n")])
@pytest.mark.parametrize("body_link", [True, False])
def test_export_preserves_frontmatter_when_converting_body(
    source_dir: Path, bom: bytes, newline: bytes, body_link: bool
) -> None:
    prefix = bom + newline.join(
        [b"---", b"type: Note", b'x-provenance: "[[b]]"', b"---", b""]
    )
    body = b"See [[b]]." if body_link else b"No body link."
    original = prefix + body + newline
    (source_dir / "source.md").write_bytes(original)
    vault = Vault(source_dir=source_dir, settings=VaultSettings())
    try:
        vault.index.build_index()
        result = build_okf_bundle(vault, ProjectConfig(source_dir=source_dir))
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            exported = archive.read("source.md")
        assert (
            exported == prefix + (b"See [b](/b.md)." if body_link else body) + newline
        )
        assert (source_dir / "source.md").read_bytes() == original
    finally:
        vault.close()


def test_export_reads_the_inspected_vault_when_config_root_differs(
    source_dir: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    shutil.copytree(source_dir, other)
    (other / "b.md").write_bytes(b"other vault secret")
    vault = Vault(source_dir=source_dir, settings=VaultSettings())
    try:
        result = build_okf_bundle(vault, ProjectConfig(source_dir=other))
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("b.md") == b"# B\n"
    finally:
        vault.close()


@pytest.mark.parametrize("name", ["index.md", "log.md"])
def test_reserved_file_with_bounded_metadata_is_reported(
    source_dir: Path, name: str
) -> None:
    content = b"---\ntype: Note\n---\n" + b"x" * 70000
    (source_dir / name).write_bytes(content)
    vault = Vault(source_dir=source_dir, settings=VaultSettings())
    try:
        result = build_okf_bundle(vault, ProjectConfig(source_dir=source_dir))
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read(name) == content
        assert result.report.uninspected[name] == "note_limit"
        assert name not in result.report.local_policy.non_admitted
        assert name not in result.report.local_policy.unknown
    finally:
        vault.close()


def test_dot_paths_are_never_exported(source_dir: Path) -> None:
    (source_dir / ".hidden.md").write_text("hidden")
    (source_dir / ".private").mkdir()
    (source_dir / ".private" / "note.md").write_text("hidden note")
    (source_dir / ".private" / "image.png").write_bytes(b"image")
    vault = Vault(
        source_dir=source_dir, settings=VaultSettings(attachment_extensions=["png"])
    )
    try:
        config = ProjectConfig(source_dir=source_dir, attachment_extensions=("png",))
        result = build_okf_bundle(vault, config, include_index_excluded=True)
        assert not any(
            part.startswith(".")
            for name in _members(result.data)
            for part in name.split("/")
        )
        assert result.report.omitted["not_part_of_bundle"] == 3
        scoped = build_okf_bundle(vault, config, folder=".private")
        assert not _members(scoped.data)
        assert scoped.report.omitted["not_part_of_bundle"] == 2
    finally:
        vault.close()


def test_hidden_directory_symlink_is_not_followed(
    source_dir: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret")
    (source_dir / ".alias").symlink_to(outside, target_is_directory=True)
    vault = Vault(source_dir=source_dir, settings=VaultSettings())
    try:
        result = build_okf_bundle(vault, ProjectConfig(source_dir=source_dir))
        assert "secret.md" not in _members(result.data)
        assert ".alias" not in _members(result.data)
    finally:
        vault.close()
