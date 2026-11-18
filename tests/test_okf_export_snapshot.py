"""Snapshot membership, bytes and consistency observations for OKF exports."""

import io
import subprocess
import zipfile
from pathlib import Path

import pytest

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
    assert set(result.report.snapshot.uncommitted_paths) == {
        "a.md",
        "deleted.md",
        "new.md",
    }


@pytest.mark.parametrize("delete", [False, True])
def test_non_git_change_during_packing_is_reported(
    tmp_path: Path, monkeypatch, delete: bool
) -> None:
    note = tmp_path / "a.md"
    note.write_text("before\n")
    (tmp_path / "clean.md").write_text("unchanged")
    original = okf_bundle._write

    def write_and_change(zf, path, data):
        original(zf, path, data)
        if path == "a.md":
            if delete:
                note.unlink()
            else:
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
    assert result.report.snapshot.commit == commit


@pytest.mark.parametrize("case", ["stale", "alias", "bom_alias"])
def test_git_export_resolves_wikilinks_from_commit(tmp_path: Path, case: str) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    folder = "" if case == "stale" else "guides"
    directory = tmp_path / folder
    directory.mkdir(exist_ok=True)
    target = directory / "target.md"
    (directory / "source.md").write_text(
        "[[target]]\n" if case == "stale" else "[[Alias]]\n"
    )
    target.write_bytes(
        b"old\n"
        if case == "stale"
        else (b"\xef\xbb\xbf" if case == "bom_alias" else b"")
        + b"---\naliases: [Alias]\n---\nbody\n"
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "before")
    target.unlink()
    if case == "stale":
        (tmp_path / "elsewhere").mkdir()
        (tmp_path / "elsewhere" / "target.md").write_text("new\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        if case == "stale":
            vault.index.build_index()
            assert (
                vault.graph.get_outlinks("source.md")[0].target_path
                == "elsewhere/target.md"
            )
        result = okf_bundle.build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder=folder
        )
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("source.md") == (
            b"[target](/target.md)\n" if case == "stale" else b"[Alias](/target.md)\n"
        )
        assert "target.md" in archive.namelist()


def test_git_query_keeps_literal_names_and_subdirectory_changes(tmp_path: Path) -> None:
    from markdown_vault_mcp.managers.git_query import git_export_snapshot

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    name = "[odd]*\nname.md"
    (vault_root / name).write_bytes(b"committed\x00bytes")
    (vault_root / ".gitignore").write_text("ignored.md\n")
    (vault_root / "staged.md").write_text("before")
    _git(tmp_path, "add", "--", "vault")
    _git(tmp_path, "commit", "-qm", "before")
    (vault_root / name).write_text("changed")
    (vault_root / "new.md").write_text("untracked")
    (vault_root / "ignored.md").write_text("ignored")
    (vault_root / "staged.md").write_text("staged")
    _git(tmp_path, "add", "vault/staged.md")
    (vault_root / "staged.md").write_text("before")
    snapshot = git_export_snapshot(vault_root)
    assert snapshot is not None
    assert snapshot.read(name) == b"committed\x00bytes"
    assert snapshot.changed == {name, "new.md", "ignored.md", "staged.md"}
    snapshot.close()


def test_missing_git_executable_keeps_plain_vault_usable(
    tmp_path: Path, monkeypatch
) -> None:
    from markdown_vault_mcp.managers import git_query

    def no_git(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(git_query, "_export_git", no_git)
    monkeypatch.setattr(git_query, "path_exists", lambda _: False)
    assert git_query.git_export_snapshot(tmp_path) is None


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
    (tmp_path / "unsupported.xyz").write_text("before")
    _git(
        tmp_path,
        "add",
        "-f",
        ".markdown_vault_mcp/secret.md",
        ".hidden.md",
        "normal.md",
        "unsupported.xyz",
    )
    _git(tmp_path, "commit", "-qm", "before")
    (tmp_path / ".markdown_vault_mcp" / "secret.md").unlink()
    (tmp_path / ".hidden.md").write_text("changed")
    (tmp_path / "unsupported.xyz").write_text("changed")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.namelist() == ["normal.md"]
    assert result.report.omitted["hidden"] == 1
    assert ".hidden.md" in result.report.snapshot.uncommitted_paths
    assert "unsupported.xyz" not in result.report.snapshot.uncommitted_paths
    assert (
        ".markdown_vault_mcp/secret.md" not in result.report.snapshot.uncommitted_paths
    )


def test_git_unborn_repository_refuses_export(tmp_path: Path) -> None:
    from markdown_vault_mcp.managers.git_query import (
        GitSnapshotUnavailableError,
        git_export_snapshot,
    )

    _git(tmp_path, "init", "-q")
    with pytest.raises(GitSnapshotUnavailableError, match="git commit"):
        git_export_snapshot(tmp_path)


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
    (tmp_path / "scope" / "a.md").write_text("dirty")
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
    assert first.uncommitted == ("scope/a.md",)
    assert (
        inspector.inspect(BundleTreeRequest(root="missing", snapshot=snapshot)).state
        == "missing"
    )


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


def test_git_snapshot_caches_blob_reads(tmp_path: Path, monkeypatch) -> None:
    from markdown_vault_mcp.managers import git_query

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "small.md").write_bytes(b"small")
    (tmp_path / "other.md").write_bytes(b"other")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")
    snapshot = git_query.git_export_snapshot(tmp_path)
    assert snapshot is not None
    original = git_query.GitExportSnapshot._read_blob
    calls = []
    readers = []

    def count(self, oid):
        calls.append(oid)
        result = original(self, oid)
        readers.append(self._process[0])
        return result

    monkeypatch.setattr(git_query.GitExportSnapshot, "_read_blob", count)
    try:
        assert snapshot.read("small.md") == b"small"
        process = snapshot._process[0]
        assert snapshot.read("small.md") == b"small"
        assert snapshot.read("other.md") == b"other"
        assert snapshot._process == [process]
        assert len(calls) == 2
        snapshot.entries["missing.md"] = ("100644", "f" * 40)
        with pytest.raises(ValueError, match="blob unavailable"):
            snapshot.read("missing.md")
    finally:
        snapshot.close()
    assert process.poll() is not None

    # Only the post-pack flag comparison reads this omitted oversized note.
    root = tmp_path / "only"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    hidden = root / ".hidden.md"
    hidden.write_bytes(b"a" * 70000)
    _git(root, "add", ".hidden.md")
    _git(root, "commit", "-qm", "initial")
    _git(root, "update-index", "--assume-unchanged", ".hidden.md")
    hidden.write_bytes(b"b" * 70000)
    readers.clear()
    vault = Vault(source_dir=root, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=root))
    finally:
        vault.close()
    assert result.report.snapshot.uncommitted_paths == [".hidden.md"]
    assert len(readers) == 1
    assert readers[0].poll() is not None


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


def test_git_large_source_and_target_resolve_links(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "source.md").write_text("[[target]]\n" + "x" * 70000)
    (tmp_path / "small.md").write_text("[[target]]\n")
    (tmp_path / "target.md").write_text("y" * 70000)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("source.md").startswith(b"[target](/target.md)\n")
        assert archive.read("small.md") == b"[target](/target.md)\n"


def test_git_assume_unchanged_edit_is_reported(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "note.md").write_text("before")
    _git(tmp_path, "add", "note.md")
    _git(tmp_path, "commit", "-qm", "initial")
    _git(tmp_path, "update-index", "--assume-unchanged", "note.md")
    (tmp_path / "note.md").write_text("after")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    assert result.report.snapshot.uncommitted_paths == ["note.md"]


def test_git_write_during_packing_is_reported(tmp_path: Path, monkeypatch) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "note.md").write_text("before")
    _git(tmp_path, "add", "note.md")
    _git(tmp_path, "commit", "-qm", "initial")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    original = okf_bundle._write

    def write_and_change(zf, path, data):
        original(zf, path, data)
        (tmp_path / "note.md").write_text("after")

    monkeypatch.setattr(okf_bundle, "_write", write_and_change)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("note.md") == b"before"
    assert result.report.snapshot.commit == commit
    assert result.report.snapshot.uncommitted_paths == ["note.md"]


@pytest.mark.parametrize(
    "flag,missing,expected",
    [
        ("--assume-unchanged", False, True),
        ("--assume-unchanged", True, True),
        ("--skip-worktree", False, True),
        ("--skip-worktree", True, False),
    ],
)
def test_git_flagged_files_are_checked_safely(
    tmp_path: Path, flag: str, missing: bool, expected: bool
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    note = tmp_path / "note.md"
    note.write_text("before")
    _git(tmp_path, "add", "note.md")
    _git(tmp_path, "commit", "-qm", "initial")
    _git(tmp_path, "update-index", flag, "note.md")
    if missing:
        note.unlink()
    else:
        note.write_text("after")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    assert ("note.md" in result.report.snapshot.uncommitted_paths) is expected
