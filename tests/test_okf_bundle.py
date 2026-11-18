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
    assert result.notes == 5


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
        assert "typed.md" in members and "b.md" in members
        assert "excluded.md" not in members and ".hidden.md" not in members
        assert "_conventions.md" not in members and "_templates/t.md" not in members
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
        hidden_only = _members(
            build_okf_bundle(vault, config, include_hidden=True).data
        )
        excluded_only = _members(
            build_okf_bundle(vault, config, include_index_excluded=True).data
        )
        assert ".hidden.md" not in hidden_only and "excluded.md" not in hidden_only
        assert ".hidden.md" not in excluded_only and "excluded.md" in excluded_only
    finally:
        vault.close()


def test_large_note_and_symlink_are_reported(source_dir: Path, tmp_path: Path) -> None:
    (source_dir / "large.md").write_text("---\ntype: Note\n---\n" + "x" * 70000)
    outside = tmp_path / "outside.md"
    outside.write_text("private")
    (source_dir / "escape.md").symlink_to(outside)
    os.mkfifo(source_dir / "x.md")
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
        assert result.report.uninspected["x.md"] == "nonregular"
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


def test_scope_normalization(vault: Vault, config: ProjectConfig) -> None:
    for scope in ("guides/./", "guides//"):
        assert _members(build_okf_bundle(vault, config, folder=scope).data) == {
            "guides/one.md"
        }


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
    from markdown_vault_mcp import bundle_inventory

    original_inventory_read = bundle_inventory.read_regular_nofollow
    original = okf_bundle._read_safe

    def read(source, path):
        if path == "page-0500.md":
            raise OSError("refused")
        return original(source, path)

    result = build_okf_bundle(vault, config)
    members = _members(result.data)
    assert "page-0000.md" in members and "page-1000.md" in members
    monkeypatch.setattr(okf_bundle, "_read_safe", read)
    with pytest.raises(OSError, match="refused"):
        build_okf_bundle(vault, config)
    monkeypatch.setattr(okf_bundle, "_read_safe", original)

    def fail_inventory(parent, filename, limit):
        if filename == "page-1000.md":
            raise OSError(5, "failed")
        return original_inventory_read(parent, filename, limit)

    monkeypatch.setattr(bundle_inventory, "read_regular_nofollow", fail_inventory)
    with pytest.raises(OSError, match=r"page-1000\.md: io_error"):
        build_okf_bundle(vault, config)


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


def test_omitted_hidden_inventory_failure_and_parser_failure(
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
        assert result.report.omitted["hidden"] == 1
        monkeypatch.setattr(
            bundle_inventory,
            "_metadata_too_complex",
            lambda _: (_ for _ in ()).throw(RuntimeError("failure")),
        )
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
        {"include_hidden": True},
        {"include_index_excluded": True, "include_hidden": True},
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


@pytest.mark.parametrize(
    ("include_hidden", "include_index_excluded"),
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_unlisted_symlinks_follow_selection_policy(
    source_dir: Path, tmp_path: Path, include_hidden: bool, include_index_excluded: bool
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("outside secret")
    bin_dir = source_dir / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python3.14").symlink_to(outside / "missing")
    (source_dir / ".cache").symlink_to(outside, target_is_directory=True)
    (source_dir / "data.bak").symlink_to(outside / "secret.md")
    vault = Vault(
        source_dir=source_dir,
        settings=VaultSettings(exclude_patterns=[".venv/**"]),
    )
    try:
        result = build_okf_bundle(
            vault,
            ProjectConfig(source_dir=source_dir),
            include_hidden=include_hidden,
            include_index_excluded=include_index_excluded,
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert "a.md" in archive.namelist()
            assert not {".venv/bin/python3.14", ".cache", "data.bak"} & set(
                archive.namelist()
            )
            assert all(
                b"outside secret" not in archive.read(n) for n in archive.namelist()
            )
        assert result.report.uninspected["data.bak"] == "hidden_symlink"
        if include_hidden:
            assert result.report.uninspected[".cache"] == "hidden_symlink"
        else:
            assert ".cache" not in result.report.uninspected
            assert result.report.omitted["hidden"] >= 2
        if include_hidden and include_index_excluded:
            assert result.report.uninspected[".venv/bin/python3.14"] == "hidden_symlink"
        else:
            assert ".venv/bin/python3.14" not in result.report.uninspected
    finally:
        vault.close()


def test_default_export_omits_hidden_venv_symlink(
    source_dir: Path, vault: Vault, config: ProjectConfig, tmp_path: Path
) -> None:
    bin_dir = source_dir / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python3.14").symlink_to(tmp_path / "missing")
    result = build_okf_bundle(vault, config)
    assert "a.md" in _members(result.data)
    assert ".venv/bin/python3.14" not in _members(result.data)
    assert result.report.omitted["hidden"] >= 1


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
        assert name not in {finding.path for finding in result.report.format_findings}
    finally:
        vault.close()
