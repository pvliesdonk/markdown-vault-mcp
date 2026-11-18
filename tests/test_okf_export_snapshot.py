"""Snapshot membership, bytes and consistency observations for OKF exports."""

import io
import subprocess
import zipfile
from pathlib import Path

from markdown_vault_mcp import okf_bundle
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.vault import Vault, VaultSettings


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_git_export_uses_commit_and_reports_uncommitted_membership(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "a.md").write_text("committed\n")
    (tmp_path / "deleted.md").write_text("committed deletion\n")
    _git(tmp_path, "add", "a.md", "deleted.md")
    _git(tmp_path, "commit", "-qm", "fixture")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "a.md").write_text("uncommitted\n")
    (tmp_path / "deleted.md").unlink()
    (tmp_path / "new.md").write_text("untracked\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("a.md") == b"committed\n"
        assert archive.read("deleted.md") == b"committed deletion\n"
        assert "new.md" not in archive.namelist()
    assert result.report.snapshot.commit == commit
    assert set(result.report.snapshot.uncommitted_paths) >= {
        "a.md",
        "deleted.md",
        "new.md",
    }


def test_non_git_change_during_packing_is_reported(tmp_path: Path, monkeypatch) -> None:
    note = tmp_path / "a.md"
    note.write_text("before\n")
    original = okf_bundle._write

    def write_and_change(zf, path, data):
        original(zf, path, data)
        note.write_text("after\n")

    monkeypatch.setattr(okf_bundle, "_write", write_and_change)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    assert result.report.snapshot.inconsistent_paths == ["a.md"]


def test_git_subdirectory_stays_on_original_commit_during_export(
    tmp_path: Path, monkeypatch
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    (vault_root / "a.md").write_text("old\n")
    (vault_root / "image.png").write_bytes(b"\x00\xffold")
    (tmp_path / "outside.md").write_text("outside\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "before")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    original = okf_bundle._write
    changed = False

    def write_and_commit(zf, path, data):
        nonlocal changed
        original(zf, path, data)
        if not changed:
            changed = True
            (vault_root / "image.png").write_bytes(b"\x00\xffnew")
            (vault_root / "later.md").write_text("later\n")
            _git(tmp_path, "add", "vault")
            _git(tmp_path, "commit", "-qm", "during")

    monkeypatch.setattr(okf_bundle, "_write", write_and_commit)
    vault = Vault(source_dir=vault_root, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(
            vault, ProjectConfig(source_dir=vault_root)
        )
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("image.png") == b"\x00\xffold"
        assert "outside.md" not in archive.namelist()
        assert "later.md" not in archive.namelist()
    assert result.report.snapshot.commit == commit


def test_export_does_not_resolve_wikilinks_from_stale_live_index(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "source.md").write_text("[[target]]\n")
    (tmp_path / "target.md").write_text("old\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "before")
    (tmp_path / "target.md").unlink()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "target.md").write_text("new\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        vault.index.build_index()
        assert (
            vault.graph.get_outlinks("source.md")[0].target_path
            == "elsewhere/target.md"
        )
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("source.md") == b"[target](/target.md)\n"


def test_git_query_keeps_literal_names_and_subdirectory_changes(tmp_path: Path) -> None:
    from markdown_vault_mcp.managers.git_query import git_export_snapshot

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    name = "[odd]*\nname.md"
    (vault_root / name).write_bytes(b"committed\x00bytes")
    _git(tmp_path, "add", "--", "vault")
    _git(tmp_path, "commit", "-qm", "before")
    (vault_root / name).write_text("changed")
    (vault_root / "new.md").write_text("untracked")
    snapshot = git_export_snapshot(vault_root)
    assert snapshot is not None
    assert snapshot.read(name) == b"committed\x00bytes"
    assert snapshot.changed == {name, "new.md"}


def test_git_subtree_uses_committed_links_and_aliases(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    guides = tmp_path / "guides"
    guides.mkdir()
    (guides / "source.md").write_text("[[Alias]]\n")
    (guides / "target.md").write_text("---\naliases: [Alias]\n---\nbody\n")
    _git(tmp_path, "add", "guides")
    _git(tmp_path, "commit", "-qm", "before")
    (guides / "target.md").unlink()
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="guides"
        )
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("source.md") == b"[Alias](/target.md)\n"
        assert "target.md" in archive.namelist()


def test_non_git_deleted_during_packing_is_inconsistent(
    tmp_path: Path, monkeypatch
) -> None:
    note = tmp_path / "a.md"
    note.write_text("before\n")
    original = okf_bundle._write

    def write_and_delete(zf, path, data):
        original(zf, path, data)
        note.unlink()

    monkeypatch.setattr(okf_bundle, "_write", write_and_delete)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    assert result.report.snapshot.inconsistent_paths == ["a.md"]


def test_missing_git_executable_keeps_plain_vault_usable(
    tmp_path: Path, monkeypatch
) -> None:
    from markdown_vault_mcp.managers import git_query

    def no_git(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(git_query, "_export_git", no_git)
    monkeypatch.setattr(git_query, "path_exists", lambda _: False)
    (tmp_path / "a.md").write_text("note")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    assert result.report.snapshot.commit is None
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("a.md") == b"note"


def test_git_tree_policy_omits_state_and_preserves_hidden_policy(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / ".markdown_vault_mcp").mkdir()
    (tmp_path / ".markdown_vault_mcp" / "secret.md").write_text("state")
    (tmp_path / ".hidden.md").write_text("hidden")
    (tmp_path / "normal.md").write_text("normal")
    _git(
        tmp_path,
        "add",
        "-f",
        ".markdown_vault_mcp/secret.md",
        ".hidden.md",
        "normal.md",
    )
    _git(tmp_path, "commit", "-qm", "before")
    (tmp_path / ".markdown_vault_mcp" / "secret.md").unlink()
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.namelist() == ["normal.md"]
    assert result.report.omitted["hidden"] == 1
    assert (
        ".markdown_vault_mcp/secret.md" not in result.report.snapshot.uncommitted_paths
    )


def test_git_unborn_repository_refuses_export(tmp_path: Path) -> None:
    import pytest

    _git(tmp_path, "init", "-q")
    (tmp_path / "new.md").write_text("uncommitted")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(ValueError, match="Git snapshot"):
            okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()


def test_git_snapshot_inventory_paginates_and_reports_scope(tmp_path: Path) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleInspector, BundleTreeRequest
    from markdown_vault_mcp.managers.git_query import git_export_snapshot

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "scope").mkdir()
    (tmp_path / "scope" / "a.md").write_text("a")
    (tmp_path / "scope" / "b.md").write_text("b")
    (tmp_path / "outside.md").write_text("outside")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "before")
    (tmp_path / "outside.md").write_text("dirty")
    snapshot = git_export_snapshot(tmp_path)
    assert snapshot is not None
    inspector = BundleInspector(tmp_path, (), frozenset(), 0)
    first = inspector.inspect(
        BundleTreeRequest(root="scope", entry_limit=1, snapshot=snapshot)
    )
    second = inspector.inspect(
        BundleTreeRequest(root="scope", cursor=first.next_cursor, snapshot=snapshot)
    )
    assert [entry.path for entry in first.entries + second.entries] == [
        "scope/a.md",
        "scope/b.md",
    ]
    assert first.uncommitted == ()
    assert (
        inspector.inspect(BundleTreeRequest(root="missing", snapshot=snapshot)).state
        == "missing"
    )


def test_git_committed_symlink_is_not_followed(tmp_path: Path) -> None:
    import pytest

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "target.md").write_text("target")
    (tmp_path / "link.md").symlink_to("target.md")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "before")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(OSError, match="symlink"):
            okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()


def test_git_submodule_is_not_silently_omitted(tmp_path: Path) -> None:
    import pytest

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "note.md").write_text("note")
    _git(tmp_path, "add", "note.md")
    _git(tmp_path, "commit", "-qm", "initial")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(tmp_path, "update-index", "--add", "--cacheinfo", f"160000,{commit},nested")
    _git(tmp_path, "commit", "-qm", "add gitlink")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(OSError, match=r"nested.*submodule"):
            okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()


def test_git_unallowed_suffix_symlink_is_not_silently_omitted(tmp_path: Path) -> None:
    import pytest

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "target.md").write_text("target")
    (tmp_path / "link.unsupported").symlink_to("target.md")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "add symlink")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(OSError, match=r"link\.unsupported.*symlink"):
            okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()


def test_git_reports_staged_and_ignored_files(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / ".gitignore").write_text("ignored.md\n")
    note = tmp_path / "note.md"
    note.write_text("committed")
    _git(tmp_path, "add", ".gitignore", "note.md")
    _git(tmp_path, "commit", "-qm", "initial")
    note.write_text("staged")
    _git(tmp_path, "add", "note.md")
    note.write_text("committed")
    (tmp_path / "ignored.md").write_text("ignored")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    assert {"note.md", "ignored.md"} <= set(result.report.snapshot.uncommitted_paths)


def test_git_snapshot_parses_aliases_once(tmp_path: Path, monkeypatch) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    for index in range(8):
        (tmp_path / f"{index}.md").write_text(
            f"---\naliases: [alias{index}]\n---\n[[alias0]]\n"
        )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")
    original = okf_bundle.parse_frontmatter
    calls = 0

    def count(text):
        nonlocal calls
        calls += 1
        return original(text)

    monkeypatch.setattr(okf_bundle, "parse_frontmatter", count)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    assert calls <= 8


def test_git_snapshot_bounded_reads_and_cache(tmp_path: Path, monkeypatch) -> None:
    from markdown_vault_mcp.managers import git_query

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "large.md").write_bytes(b"x" * 200000)
    (tmp_path / "small.md").write_bytes(b"small")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")
    snapshot = git_query.git_export_snapshot(tmp_path)
    assert snapshot is not None
    original = git_query._export_git
    calls = []

    def count(root, *args, **kwargs):
        calls.append(args)
        return original(root, *args, **kwargs)

    monkeypatch.setattr(git_query, "_export_git", count)
    assert snapshot.read("large.md", 12) == b"x" * 12
    assert not calls
    assert snapshot.read("small.md") == b"small"
    assert snapshot.read("small.md") == b"small"
    assert len(calls) == 1


def test_git_export_logs_clean_and_dirty_snapshot(tmp_path: Path, caplog) -> None:
    import logging

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    note = tmp_path / "note.md"
    note.write_text("committed")
    _git(tmp_path, "add", "note.md")
    _git(tmp_path, "commit", "-qm", "initial")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with caplog.at_level(logging.INFO, logger=okf_bundle.__name__):
            okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
            note.write_text("changed")
            okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    records = [
        record for record in caplog.records if record.msg.startswith("okf_bundle_built")
    ]
    assert [record.levelno for record in records] == [logging.INFO, logging.WARNING]
    assert (
        f"commit={commit[:12]} uncommitted=1 inconsistent=0" in records[1].getMessage()
    )
    assert "note.md" not in records[1].getMessage()


def test_git_inventory_rejects_large_note_without_reading_blob(
    tmp_path: Path, monkeypatch
) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleInspector, BundleTreeRequest
    from markdown_vault_mcp.managers import git_query

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "large.md").write_bytes(b"x" * 100)
    _git(tmp_path, "add", "large.md")
    _git(tmp_path, "commit", "-qm", "initial")
    snapshot = git_query.git_export_snapshot(tmp_path)
    assert snapshot is not None

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("oversized blob was read")

    monkeypatch.setattr(git_query.GitExportSnapshot, "read", unexpected_read)
    inspector = BundleInspector(tmp_path, (), frozenset(), 10)
    page = inspector.inspect(BundleTreeRequest(root="", snapshot=snapshot))
    assert [(entry.path, entry.reason) for entry in page.entries] == [
        ("large.md", "note_limit")
    ]
