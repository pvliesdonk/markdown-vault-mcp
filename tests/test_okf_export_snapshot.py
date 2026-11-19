"""Snapshot membership, bytes and consistency observations for OKF exports."""

import hashlib
import io
import os
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
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
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


@pytest.mark.parametrize("case", ["stale", "alias", "bom_alias", "unavailable"])
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
    if case == "unavailable":
        oid = _git(tmp_path, "rev-parse", "HEAD")
        _git(
            tmp_path,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{oid},vendor/missing.md",
        )
        _git(tmp_path, "commit", "-qm", "unavailable child")
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


def test_git_and_live_export_resolve_links_equally(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    files = {
        "docs/source.md": "[[docs/exact]]\n[[deep/suffix]]\n[[Alias]]\n[[./relative]]\n[[duplicate]]\n",
        "docs/exact.md": "exact",
        "elsewhere/deep/suffix.md": "suffix",
        "alias.md": "---\naliases: [Alias]\n---\nalias",
        "docs/relative.md": "relative",
        "docs/duplicate.md": "near",
        "elsewhere/duplicate.md": "far",
    }
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    config = ProjectConfig(source_dir=tmp_path)
    try:
        vault.index.build_index()
        live = okf_bundle.build_okf_bundle(vault, config)
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "user.name", "Fixture")
        _git(tmp_path, "config", "user.email", "fixture@example.invalid")
        _git(tmp_path, "add", *files)
        _git(tmp_path, "commit", "-qm", "initial")
        committed = okf_bundle.build_okf_bundle(vault, config)
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(live.data)) as archive:
        live_note = archive.read("docs/source.md")
    with zipfile.ZipFile(io.BytesIO(committed.data)) as archive:
        committed_note = archive.read("docs/source.md")
    assert live_note == committed_note
    assert live_note == (
        b"[docs/exact](/docs/exact.md)\n"
        b"[deep/suffix](/elsewhere/deep/suffix.md)\n"
        b"[Alias](/alias.md)\n"
        b"[./relative](/docs/relative.md)\n"
        b"[duplicate](/docs/duplicate.md)\n"
    )


def test_git_query_keeps_literal_names_and_subdirectory_changes(tmp_path: Path) -> None:
    from markdown_vault_mcp.git.query import git_export_snapshot

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
    from markdown_vault_mcp.git.query import git_snapshot_changes

    assert git_snapshot_changes(vault_root, snapshot.commit)[0] == {
        name,
        "new.md",
        "ignored.md",
        "staged.md",
    }
    snapshot.close()


def test_missing_git_executable_keeps_plain_vault_usable(
    tmp_path: Path, monkeypatch
) -> None:
    from markdown_vault_mcp.git import query as git_query

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
    from markdown_vault_mcp.git.query import git_export_snapshot

    _git(tmp_path, "init", "-q")
    with pytest.raises(ValueError, match="git commit"):
        git_export_snapshot(tmp_path)


@pytest.mark.usefixtures("isolated_git")
def test_git_dubious_ownership_refuses_with_actionable_reason(
    tmp_path: Path, monkeypatch
) -> None:
    from markdown_vault_mcp.git.query import git_export_snapshot

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "note.md").write_text("note")
    _git(tmp_path, "add", "note.md")
    _git(tmp_path, "commit", "-qm", "initial")
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    with pytest.raises(ValueError, match=r"(?s)dubious ownership.*safe\.directory"):
        git_export_snapshot(tmp_path)
    from markdown_vault_mcp.git import query as git_query

    monkeypatch.setattr(
        git_query,
        "_export_git",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, stdout=b"", stderr=b"unexpected discovery failure"
        ),
    )
    with pytest.raises(ValueError, match="unexpected discovery failure") as error:
        git_export_snapshot(tmp_path)
    assert type(error.value) is ValueError


def test_git_snapshot_inventory_paginates_and_reports_scope(tmp_path: Path) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleInspector, BundleTreeRequest
    from markdown_vault_mcp.git.query import git_export_snapshot

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
    from dataclasses import replace

    from markdown_vault_mcp.git.query import git_snapshot_changes

    changed, flagged = git_snapshot_changes(tmp_path, snapshot.commit)
    snapshot = replace(snapshot, changed=changed, flagged=flagged)
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


def test_git_snapshot_reuses_and_closes_batch_reader(tmp_path: Path) -> None:
    from markdown_vault_mcp.git import query as git_query

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "small.md").write_bytes(b"small")
    (tmp_path / "other.md").write_bytes(b"other")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")
    snapshot = git_query.git_export_snapshot(tmp_path)
    assert snapshot is not None
    try:
        assert snapshot.read("small.md") == b"small"
        process = snapshot._process[snapshot.root]
        assert snapshot.read("small.md") == b"small"
        assert snapshot.read("other.md") == b"other"
        assert snapshot._process == {snapshot.root: process}
        snapshot.entries["missing.md"] = ("100644", "f" * 40)
        with pytest.raises(ValueError, match="blob unavailable"):
            snapshot.read("missing.md")
    finally:
        snapshot.close()
    assert process.poll() is not None


def test_git_inventory_rejects_large_note_without_reading_blob(
    tmp_path: Path, monkeypatch
) -> None:
    from markdown_vault_mcp.bundle_inventory import BundleInspector, BundleTreeRequest
    from markdown_vault_mcp.git import query as git_query

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


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "bad_utf8_target",
        "bad_yaml_target",
        "bad_utf8_source",
        "bad_yaml_source",
    ],
)
def test_git_large_source_and_target_resolve_links(tmp_path: Path, case: str) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    bad = b"\xff\n" if "utf8" in case else b"---\na: [unterminated\n---\n"
    source = (bad if case.endswith("source") else b"") + b"[[target]]\n" + b"x" * 70000
    target = (
        bad if case.endswith("target") else b"---\naliases: [BigAlias]\n---\n"
    ) + b"y" * 70000
    (tmp_path / "source.md").write_bytes(source)
    (tmp_path / "small.md").write_text("[[target]]\n[[BigAlias]]\n")
    (tmp_path / "target.md").write_bytes(target)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        if case == "valid":
            assert archive.read("source.md").startswith(b"[target](/target.md)\n")
        else:
            assert archive.read("source.md") == source
        assert archive.read("small.md") == (
            b"[[target]]\n[[BigAlias]]\n"
            if case.endswith("target")
            else b"[target](/target.md)\n[BigAlias](/target.md)\n"
        )

    if case != "valid":
        path = "target.md" if case.endswith("target") else "source.md"
        assert result.report.uninspected[path] == "note_limit"


def test_git_write_during_packing_is_reported(tmp_path: Path, monkeypatch) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "a.md").write_text("first")
    (tmp_path / "note.md").write_text("before")
    _git(tmp_path, "add", "a.md", "note.md")
    _git(tmp_path, "commit", "-qm", "initial")
    original = okf_bundle._write

    def write_and_change(zf, path, data):
        original(zf, path, data)
        if path == "a.md":
            (tmp_path / "note.md").write_text("after")

    monkeypatch.setattr(okf_bundle, "_write", write_and_change)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("note.md") == b"before"
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


@pytest.mark.parametrize("flag", ["h", "S", "s"])
@pytest.mark.parametrize("edited", [False, True])
@pytest.mark.parametrize("root_kind", ["root", "nested", "child"])
def test_git_flagged_crlf_uses_git_normalization(
    tmp_path: Path, flag: str, edited: bool, root_kind: str
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / ".gitattributes").write_text("*.md text eol=crlf\n")
    folder = repo / "vault" if root_kind == "nested" else repo
    folder.mkdir(exist_ok=True)
    (folder / "note.md").write_bytes(b"original\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "initial")
    if root_kind == "child":
        parent = tmp_path / "parent"
        parent.mkdir()
        _git(parent, "init", "-q")
        _git(parent, "config", "user.name", "Fixture")
        _git(parent, "config", "user.email", "fixture@example.invalid")
        _git(
            parent,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(repo),
            "vendor",
        )
        _git(parent, "commit", "-qm", "parent")
        folder = parent / "vendor"
        vault_root = parent
        repo = folder
    else:
        vault_root = folder
    note = folder / "note.md"
    note.write_bytes(b"changed\r\n" if edited else b"original\r\n")
    note_path = "vault/note.md" if root_kind == "nested" else "note.md"
    _git(
        repo,
        "update-index",
        "--assume-unchanged" if flag == "h" else "--skip-worktree",
        note_path,
    )
    if flag == "s":
        _git(repo, "update-index", "--assume-unchanged", note_path)
    assert _git(repo, "ls-files", "-v", "--", note_path).startswith(flag + " ")
    vault = Vault(source_dir=vault_root, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(
            vault, ProjectConfig(source_dir=vault_root)
        )
    finally:
        vault.close()
    path = "vendor/note.md" if root_kind == "child" else "note.md"
    assert result.report.snapshot.uncommitted_paths == ([path] if edited else [])


@pytest.mark.parametrize("name", ["note.md", "image.png"])
@pytest.mark.parametrize("version", ["git-lfs.github.com", "hawser.github.com"])
def test_git_lfs_pointer_is_refused(tmp_path: Path, name: str, version: str) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / name).write_text(
        f"version https://{version}/spec/v1\noid sha256:" + "a" * 64 + "\nsize 3\n"
    )
    _git(tmp_path, "add", name)
    _git(tmp_path, "commit", "-qm", "initial")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(ValueError, match="LFS"):
            okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()


def test_git_untracked_only_folder_exports_empty_commit(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "base.md").write_text("base")
    _git(tmp_path, "add", "base.md")
    _git(tmp_path, "commit", "-qm", "initial")
    (tmp_path / "drafts").mkdir()
    (tmp_path / "drafts" / "new.md").write_text("draft")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="drafts"
        )
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.namelist() == []
    assert result.report.snapshot.uncommitted_paths == ["drafts/new.md"]


def test_git_query_child_readers_close_and_refuse_missing_blob(tmp_path: Path) -> None:
    child = tmp_path / "child"
    parent = tmp_path / "parent"
    child.mkdir()
    parent.mkdir()
    for repo in (child, parent):
        _git(repo, "init", "-q")
        _git(repo, "config", "user.name", "Fixture")
        _git(repo, "config", "user.email", "fixture@example.invalid")
    (child / "note.md").write_text("recorded")
    _git(child, "add", ".")
    _git(child, "commit", "-qm", "child")
    _git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(child),
        "vendor/sub",
    )
    _git(parent, "commit", "-qm", "parent")
    populated = parent / "vendor/sub"
    from markdown_vault_mcp.git.query import git_export_snapshot

    snapshot = git_export_snapshot(parent)
    assert snapshot is not None
    try:
        snapshot.read(".gitmodules")
        assert snapshot.read("vendor/sub/note.md") == b"recorded"
        processes = list(snapshot._process.values())
        assert len(processes) == 2

        snapshot.entries["vendor/sub/missing.md"] = ("100644", "f" * 40)
        snapshot.repos["vendor/sub/missing.md"] = populated
        with pytest.raises(ValueError, match="submodule"):
            snapshot.read("vendor/sub/missing.md")
    finally:
        snapshot.close()
    assert all(process.poll() is not None for process in processes)


@pytest.mark.parametrize("scope", ["", "plain", "plain/sub"])
def test_git_untracked_nested_repository_is_reported(
    tmp_path: Path, scope: str
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "base.md").write_text("base")
    _git(tmp_path, "add", "base.md")
    _git(tmp_path, "commit", "-qm", "initial")
    nested = tmp_path / "plain"
    nested.mkdir()
    _git(nested, "init", "-q")
    (nested / "note.md").write_text("untracked")
    (nested / "sub").mkdir()
    (nested / "sub" / "note.md").write_text("untracked")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder=scope
        )
    finally:
        vault.close()
    marker = scope + "/" if scope.startswith("plain/") else "plain/"
    assert marker in result.report.snapshot.uncommitted_paths


@pytest.mark.parametrize("root_kind", ["absolute", "relative", "symlink"])
def test_git_nested_submodules_use_recorded_child_commits(
    tmp_path: Path, root_kind: str, monkeypatch
) -> None:
    leaf, child, parent = (tmp_path / name for name in ("leaf", "child", "parent"))
    for repo in (leaf, child, parent):
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.name", "Fixture")
        _git(repo, "config", "user.email", "fixture@example.invalid")
    (child / "note.md").write_text("child commit")
    (child / ".gitignore").write_text("ignored.md\n")
    (leaf / "note.md").write_text("leaf commit")
    _git(leaf, "add", ".")
    _git(leaf, "commit", "-qm", "leaf")
    _git(
        child,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(leaf),
        "nested",
    )
    _git(child, "add", "note.md", ".gitignore")
    _git(child, "commit", "-qm", "child")
    _git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(child),
        "vendor",
    )
    _git(parent, "commit", "-qm", "parent")
    _git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "update",
        "--init",
        "--recursive",
        "-q",
    )
    populated = parent / "vendor"
    _git(populated, "config", "user.name", "Fixture")
    _git(populated, "config", "user.email", "fixture@example.invalid")
    (populated / "note.md").write_text("child later")
    _git(populated, "add", "note.md")
    _git(populated, "commit", "-qm", "later child")
    (populated / "new.md").write_text("untracked")
    (populated / "ignored.md").write_text("ignored")
    (parent / "vendor/nested/note.md").write_text("uncommitted")
    if root_kind == "relative":
        monkeypatch.chdir(tmp_path)
        root = Path("parent")
    elif root_kind == "symlink":
        root = tmp_path / "alias"
        root.symlink_to(parent, target_is_directory=True)
    else:
        root = parent
    vault = Vault(source_dir=root, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=root))
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("vendor/nested/note.md") == b"leaf commit"
        assert archive.read("vendor/note.md") == b"child commit"
        assert "vendor/new.md" not in archive.namelist()
    assert set(result.report.snapshot.uncommitted_paths) == {
        "vendor/",
        "vendor/nested/note.md",
        "vendor/note.md",
        "vendor/new.md",
        "vendor/ignored.md",
    }


@pytest.mark.parametrize("scope", ["", "vendor", "vendor/sub"])
@pytest.mark.parametrize("symlink", [False, True])
def test_git_unavailable_submodule_refuses_with_guidance(
    tmp_path: Path, scope: str, symlink: bool, monkeypatch
) -> None:

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "note.md").write_text("base")
    _git(tmp_path, "add", "note.md")
    _git(tmp_path, "commit", "-qm", "initial")
    commit = _git(tmp_path, "rev-parse", "HEAD")
    _git(
        tmp_path, "update-index", "--add", "--cacheinfo", f"160000,{commit},vendor/sub"
    )
    _git(tmp_path, "commit", "-qm", "gitlink")
    if symlink:
        from markdown_vault_mcp.git import query as git_query

        (tmp_path / "vendor").mkdir()
        (tmp_path / "vendor/sub").symlink_to(tmp_path, target_is_directory=True)
        original_git = git_query._export_git

        def checked_git(root, *args, **kwargs):
            assert root != tmp_path / "vendor/sub"
            return original_git(root, *args, **kwargs)

        monkeypatch.setattr(git_query, "_export_git", checked_git)
    else:
        (tmp_path / "vendor/sub").mkdir(parents=True)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(ValueError, match="git submodule update --init"):
            okf_bundle.build_okf_bundle(
                vault, ProjectConfig(source_dir=tmp_path), folder=scope
            )
    finally:
        vault.close()


@pytest.mark.parametrize("scope", ["docs", "vendor"])
def test_submodule_disappearing_respects_scope(
    tmp_path: Path, monkeypatch, scope: str
) -> None:
    import shutil

    child, parent = tmp_path / "child", tmp_path / "parent"
    for repo in (child, parent):
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.name", "Fixture")
        _git(repo, "config", "user.email", "fixture@example.invalid")
    (child / "note.md").write_text("child")
    _git(child, "add", ".")
    _git(child, "commit", "-qm", "child")
    (parent / "docs").mkdir()
    (parent / "docs" / "note.md").write_text("selected")
    _git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(child),
        "vendor",
    )
    _git(parent, "add", "docs")
    _git(parent, "commit", "-qm", "parent")
    original = okf_bundle._write

    def write_then_remove(zf, path, data):
        original(zf, path, data)
        shutil.rmtree(parent / "vendor")

    monkeypatch.setattr(okf_bundle, "_write", write_then_remove)
    vault = Vault(source_dir=parent, settings=VaultSettings())
    try:
        if scope == "vendor":
            with pytest.raises(ValueError, match="git submodule update --init"):
                okf_bundle.build_okf_bundle(
                    vault, ProjectConfig(source_dir=parent), folder=scope
                )
        else:
            result = okf_bundle.build_okf_bundle(
                vault, ProjectConfig(source_dir=parent), folder=scope
            )
            with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
                assert archive.read("note.md") == b"selected"
            assert result.report.snapshot.uncommitted_paths == []
    finally:
        vault.close()


@pytest.mark.parametrize("scope", ["", "vendor/sub", "vendor/sub/nested"])
@pytest.mark.parametrize("change", ["staged", "empty_commit"])
def test_git_gitlink_change_is_reported(
    tmp_path: Path, scope: str, change: str
) -> None:
    child, parent = tmp_path / "child", tmp_path / "parent"
    for repo in (child, parent):
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.name", "Fixture")
        _git(repo, "config", "user.email", "fixture@example.invalid")
    (child / "note.md").write_text("old")
    (child / "nested").mkdir()
    (child / "nested" / "note.md").write_text("nested")
    _git(child, "add", ".")
    _git(child, "commit", "-qm", "old")
    _git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(child),
        "vendor/sub",
    )
    _git(parent, "commit", "-qm", "parent")
    worktree = parent / "vendor/sub"
    _git(worktree, "config", "user.name", "Fixture")
    _git(worktree, "config", "user.email", "fixture@example.invalid")
    original = _git(worktree, "rev-parse", "HEAD")
    _git(parent, "config", "submodule.vendor/sub.ignore", "all")
    if change == "staged":
        (worktree / "note.md").write_text("new")
        _git(worktree, "add", "note.md")
        _git(worktree, "commit", "-qm", "new child")
        _git(parent, "add", "vendor/sub")
        _git(worktree, "checkout", "-q", "--detach", original)
    else:
        _git(worktree, "commit", "--allow-empty", "-qm", "empty child commit")
    vault = Vault(source_dir=parent, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(
            vault, ProjectConfig(source_dir=parent), folder=scope
        )
    finally:
        vault.close()
    marker = scope + "/" if scope.startswith("vendor/sub/") else "vendor/sub/"
    assert result.report.snapshot.uncommitted_paths == [marker]
    archive_path = "vendor/sub/note.md" if not scope else "note.md"
    expected = b"nested" if scope.endswith("/nested") else b"old"
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read(archive_path) == expected


def test_gitlink_depth_is_bounded_before_discovery(tmp_path: Path, monkeypatch) -> None:
    from markdown_vault_mcp.git import query as git_query

    path = "/".join(["nested"] * 33)
    child = tmp_path / path
    child.mkdir(parents=True)
    _git(child, "init", "-q")
    _git(child, "config", "user.name", "Fixture")
    _git(child, "config", "user.email", "fixture@example.invalid")
    (child / "note.md").write_text("child")
    _git(child, "add", "note.md")
    _git(child, "commit", "-qm", "child")
    oid = _git(child, "rev-parse", "HEAD")
    snapshot = git_query.GitExportSnapshot(
        "a" * 40, {path: ("160000", oid)}, set(), tmp_path, {path: 0}
    )

    def unexpected(*_args, **_kwargs):
        raise AssertionError("deep child must not be discovered")

    monkeypatch.setattr(git_query, "_export_git", unexpected)
    git_query._expand_gitlinks(tmp_path, snapshot)
    assert snapshot.entries[path][0] == "160000"
    assert not snapshot.children


def test_git_snapshot_resolves_against_admitted_commit_notes(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "docs").mkdir()
    (tmp_path / "target.md").write_text("[[index]]\n")
    (tmp_path / "docs" / "target.md").write_text("---\ntitle: Target\n---\nBody\n")
    (tmp_path / "index.md").write_text("---\nokf_version: '0.2'\n---\n# Index\n")
    (tmp_path / "log.md").write_text("[[target]] and [[index]]\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(okf_mode="on", required_frontmatter=["title"]),
    )
    try:
        vault.index.build_index()
        assert vault.graph.get_outlinks("log.md")[0].target_path == "docs/target.md"
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert (
            archive.read("log.md")
            == b"[target](/docs/target.md) and [index](/index.md)\n"
        )
        assert archive.read("target.md") == b"[[index]]\n"


@pytest.mark.parametrize(
    ("committed_active", "mode", "expected"),
    [
        (True, "auto", True),
        (False, "auto", False),
        (False, "on", True),
        (True, "off", False),
    ],
)
def test_git_reserved_admission_uses_committed_index(
    tmp_path: Path, committed_active: bool, mode: str, expected: bool
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    declaration = "---\nokf_version: '0.2'\n---\n"
    (tmp_path / "index.md").write_text(
        declaration if committed_active else "inactive\n"
    )
    (tmp_path / "log.md").write_text("[[target]]\n")
    (tmp_path / "target.md").write_text("---\ntitle: Target\n---\n[[log]]\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    (tmp_path / "index.md").write_text(
        "inactive\n" if committed_active else declaration
    )
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(okf_mode=mode, required_frontmatter=["title"]),
    )
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("log.md") == (
            b"[target](/target.md)\n" if expected else b"[[target]]\n"
        )
        assert archive.read("target.md").endswith(
            b"[log](/log.md)\n" if expected else b"[[log]]\n"
        )


def test_git_denied_root_index_cannot_enable_scoped_admission(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    index = tmp_path / "index.md"
    index.write_text("---\nokf_version: '0.2'\n---\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "log.md").write_text("[[docs/target]]\n")
    (tmp_path / "docs" / "target.md").write_text("---\ntitle: Target\n---\nBody\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    index.chmod(0)
    try:
        try:
            index.read_bytes()
        except PermissionError:
            pass
        else:
            pytest.skip("filesystem permits reading chmod-000 files")
        vault = Vault(
            source_dir=tmp_path,
            settings=VaultSettings(okf_mode="auto", required_frontmatter=["title"]),
        )
        try:
            try:
                result = okf_bundle.build_okf_bundle(
                    vault, ProjectConfig(source_dir=tmp_path), folder="docs"
                )
            except (OSError, ValueError):
                return
        finally:
            vault.close()
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("log.md") == b"[[docs/target]]\n"
    finally:
        index.chmod(0o600)


def test_git_flagged_required_clean_filter_failure_refuses_export(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / ".gitattributes").write_text("note.md filter=broken\n")
    note = tmp_path / "note.md"
    note.write_text("before\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")
    _git(tmp_path, "update-index", "--assume-unchanged", "note.md")
    _git(tmp_path, "config", "filter.broken.clean", "false")
    _git(tmp_path, "config", "filter.broken.required", "true")
    note.write_text("after\n")
    assert _git(tmp_path, "diff", "--name-only", "HEAD") == ""
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(ValueError, match="normalize"):
            okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()


def test_git_snapshot_ignores_foreign_git_dir(tmp_path: Path, monkeypatch) -> None:
    vault_root = tmp_path / "vault"
    foreign = tmp_path / "foreign"
    vault_root.mkdir()
    foreign.mkdir()
    for root, content in ((vault_root, "vault bytes\n"), (foreign, "foreign bytes\n")):
        _git(root, "init", "-q")
        _git(root, "config", "user.name", "Fixture")
        _git(root, "config", "user.email", "fixture@example.invalid")
        (root / "note.md").write_text(content)
        _git(root, "add", "note.md")
        _git(root, "commit", "-qm", "fixture")
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    vault = Vault(source_dir=vault_root, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(
            vault, ProjectConfig(source_dir=vault_root)
        )
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("note.md") == b"vault bytes\n"


def test_git_snapshot_tree_enumeration_is_scoped_to_vault(
    tmp_path: Path, monkeypatch
) -> None:
    from markdown_vault_mcp.git import query as git_query

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    (vault_root / "inside.md").write_text("inside\n")
    (tmp_path / "outside.md").write_text("outside\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    tree_names = []
    original = git_query._export_git

    def observe_tree(root, *args, **kwargs):
        result = original(root, *args, **kwargs)
        if args and args[0] == "ls-tree":
            tree_names.extend(
                record.split(b"\t", 1)[1]
                for record in result.stdout.split(b"\0")
                if record
            )
        return result

    monkeypatch.setattr(git_query, "_export_git", observe_tree)
    snapshot = git_query.git_export_snapshot(vault_root)
    assert snapshot is not None
    try:
        assert set(snapshot.entries) == {"inside.md"}
        assert tree_names == [b"vault/inside.md"]
    finally:
        snapshot.close()


@pytest.mark.parametrize("kind", ["note", "large_note", "attachment"])
@pytest.mark.parametrize("live_state", ["matching", "mismatching", "unavailable"])
@pytest.mark.parametrize("nested", [False, True])
def test_git_lfs_pointer_uses_only_verified_live_bytes(
    tmp_path: Path, kind: str, live_state: str, nested: bool
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    vault_root = tmp_path / "vault" if nested else tmp_path
    vault_root.mkdir(exist_ok=True)
    name = "target.md" if kind != "attachment" else "image.png"
    content = (
        b"---\ntitle: Target\naliases: [Alias]\n---\n"
        + (b"large body\n" * 7000 if kind == "large_note" else b"Target body\n")
        if kind != "attachment"
        else b"\x89PNG\r\nverified image bytes"
    )
    pointer = (
        "version https://git-lfs.github.com/spec/v1\n"
        f"oid sha256:{hashlib.sha256(content).hexdigest()}\n"
        f"size {len(content)}\n"
    )
    path = vault_root / name
    path.write_text(pointer)
    if kind != "attachment":
        (vault_root / "source.md").write_text("---\ntitle: Source\n---\n[[Alias]]\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "pointer")
    if live_state == "matching":
        path.write_bytes(content)
    elif live_state == "mismatching":
        path.write_bytes(b"wrong bytes")
    else:
        path.unlink()
    vault = Vault(
        source_dir=vault_root, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        if live_state != "matching":
            with pytest.raises(ValueError, match="LFS"):
                okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=vault_root))
            return
        result = okf_bundle.build_okf_bundle(
            vault, ProjectConfig(source_dir=vault_root)
        )
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read(name) == content
        if kind != "attachment":
            assert archive.read("source.md") == (
                b"---\ntitle: Source\n---\n[Alias](/target.md)\n"
            )


def test_git_lfs_alias_outside_selected_folder_uses_verified_note(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    content = b"---\ntitle: Target\naliases: [Alias]\n---\n" + b"large body\n" * 7000
    target = tmp_path / "target.md"
    target.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        f"oid sha256:{hashlib.sha256(content).hexdigest()}\n"
        f"size {len(content)}\n"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "source.md").write_text("---\ntitle: Source\n---\n[[Alias]]\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "pointer")
    target.write_bytes(content)
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        result = okf_bundle.build_okf_bundle(
            vault,
            ProjectConfig(source_dir=tmp_path),
            folder="docs",
            external_base="https://example.org/vault",
        )
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert "target.md" not in archive.namelist()
        assert archive.read("source.md") == (
            b"---\ntitle: Source\n---\n[Alias](https://example.org/vault/target.md)\n"
        )


@pytest.mark.parametrize("folder", ["", "docs"])
def test_git_snapshot_yaml_aliases_match_index_admission(
    tmp_path: Path, folder: str
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    directory = tmp_path / folder
    directory.mkdir(exist_ok=True)
    (directory / "source.md").write_text(
        "---\ntitle: Source\nx: &anchor value\ny: *anchor\n---\n[[Alias]]\n"
    )
    (directory / "target.md").write_text(
        "---\ntitle: Target\nx: &anchor value\ny: *anchor\naliases: [Alias]\n---\nTarget\n"
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        vault.index.build_index()
        assert vault.graph.get_outlinks(f"{folder}/source.md".lstrip("/"))[
            0
        ].target_path == f"{folder}/target.md".lstrip("/")
        result = okf_bundle.build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder=folder
        )
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("source.md").endswith(b"[Alias](/target.md)\n")


@pytest.mark.parametrize("name", [".hidden.md", "excluded.md"])
@pytest.mark.parametrize("folder", ["", "docs"])
def test_git_omitted_missing_lfs_note_does_not_block_export(
    tmp_path: Path, name: str, folder: str
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    directory = tmp_path / folder
    directory.mkdir(exist_ok=True)
    (directory / "good.md").write_text("ordinary\n")
    pointer = directory / name
    pointer.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:"
        + "a" * 64
        + "\nsize 10\n"
    )
    _git(tmp_path, "add", "-f", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    vault = Vault(
        source_dir=tmp_path,
        settings=VaultSettings(exclude_patterns=[f"{folder}/{name}".lstrip("/")]),
    )
    try:
        vault.index.build_index()
        pointer.unlink()
        result = okf_bundle.build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder=folder
        )
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert "good.md" in archive.namelist()
        assert name not in archive.namelist()


def test_git_relative_link_does_not_require_unrelated_missing_lfs(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "source.md").write_text("[[./target]]\n")
    (tmp_path / "docs" / "target.md").write_text("target\n")
    other = tmp_path / "unrelated.md"
    other.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:"
        + "a" * 64
        + "\nsize 10\n"
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    other.unlink()
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="docs"
        )
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("source.md") == b"[./target](/target.md)\n"


@pytest.mark.parametrize("state", ["missing", "unreadable", "present"])
def test_git_empty_lfs_object_requires_successful_read(
    tmp_path: Path, monkeypatch, state: str
) -> None:
    from markdown_vault_mcp.git import query as git_query

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    path = tmp_path / "empty.md"
    path.write_text(
        f"version https://git-lfs.github.com/spec/v1\noid sha256:{hashlib.sha256(b'').hexdigest()}\nsize 0\n"
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    if state == "missing":
        path.unlink()
    else:
        path.write_bytes(b"")
    if state == "unreadable":

        def denied(*_args, **_kwargs):
            raise PermissionError("fixture refusal")

        monkeypatch.setattr(git_query, "read_regular_nofollow", denied)
    snapshot = git_query.git_export_snapshot(tmp_path)
    assert snapshot is not None
    try:
        if state == "present":
            assert snapshot.read("empty.md") == b""
        else:
            with pytest.raises(ValueError, match="LFS"):
                snapshot.read("empty.md")
    finally:
        snapshot.close()


def test_git_included_excluded_complex_note_stays_unadmitted(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "excluded.md").write_text(
        "---\nx: &anchor value\ny: *anchor\n---\n[[target]]\n"
    )
    (tmp_path / "target.md").write_text("target\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(exclude_patterns=["excluded.md"])
    )
    try:
        vault.index.build_index()
        result = okf_bundle.build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), include_index_excluded=True
        )
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("excluded.md").endswith(b"[[target]]\n")


@pytest.mark.parametrize(
    ("target", "root_target"),
    [("../target", True), ("target", True), ("target", False), ("alias", False)],
)
def test_git_relative_outside_target_ignores_unrelated_missing_lfs(
    tmp_path: Path,
    target: str,
    root_target: bool,
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "source.md").write_text(f"[[{target}]]\n")
    ((tmp_path if root_target else tmp_path / "docs") / "target.md").write_text(
        "target\n"
    )
    other = tmp_path / "unrelated.md"
    other.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:"
        + "a" * 64
        + "\nsize 10\n"
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    other.unlink()
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        if target == "alias":
            with pytest.raises(ValueError, match="LFS"):
                okf_bundle.build_okf_bundle(
                    vault, ProjectConfig(source_dir=tmp_path), folder="docs"
                )
            return
        result = okf_bundle.build_okf_bundle(
            vault,
            ProjectConfig(source_dir=tmp_path),
            folder="docs",
            external_base="https://example.org/vault",
        )
        with pytest.raises(ValueError, match="LFS"):
            okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read("source.md") == (
            f"[{target}](https://example.org/vault/target.md)\n".encode()
            if root_target
            else b"[target](/target.md)\n"
        )


@pytest.mark.parametrize("staged", [False, True])
def test_git_scoped_dirty_symlink_is_reported_without_following_target(
    tmp_path: Path, staged: bool
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "base.md").write_text("base\n")
    (tmp_path / "docs" / "deleted").mkdir()
    (tmp_path / "docs" / "deleted" / "note.md").write_text("deleted\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    missing_target = tmp_path.parent / "no-such-symlink-target"
    (tmp_path / "docs" / "new.md").symlink_to(missing_target)
    (tmp_path / "docs" / "ignored.bin").symlink_to(missing_target)
    (tmp_path / "docs" / "deleted" / "note.md").unlink()
    (tmp_path / "docs" / "deleted").rmdir()
    (tmp_path / "outside.md").symlink_to(missing_target)
    internal = tmp_path / "docs" / ".markdown_vault_mcp"
    internal.mkdir(exist_ok=True)
    (internal / "secret.md").symlink_to(missing_target)
    assert "docs/.markdown_vault_mcp/secret.md" in _git(
        tmp_path, "ls-files", "--others"
    )
    if staged:
        _git(tmp_path, "add", "docs/new.md")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), folder="docs"
        )
    finally:
        vault.close()
    assert result.report.snapshot.uncommitted_paths == [
        "docs/deleted/note.md",
        "docs/new.md",
    ]
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert sorted(archive.namelist()) == ["base.md", "deleted/note.md"]


def test_git_dirty_report_omits_unreadable_file_but_keeps_symlink(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "base.md").write_text("base\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    denied = tmp_path / "denied.md"
    denied.write_text("unreadable\n")
    (tmp_path / "link.md").symlink_to(tmp_path / "missing")
    denied.chmod(0)
    try:
        try:
            denied.read_bytes()
        except PermissionError:
            pass
        else:
            pytest.skip("filesystem permits reading chmod-000 files")
        vault = Vault(source_dir=tmp_path, settings=VaultSettings())
        try:
            result = okf_bundle.build_okf_bundle(
                vault, ProjectConfig(source_dir=tmp_path)
            )
        finally:
            vault.close()
        assert result.report.snapshot.uncommitted_paths == ["link.md"]
    finally:
        denied.chmod(0o600)


@pytest.mark.parametrize("kind", ["symlink", "nonregular"])
def test_git_visible_replacement_refusal_names_path_and_reason(
    tmp_path: Path, kind: str
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    note = tmp_path / "a.md"
    note.write_text("committed\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    note.unlink()
    if kind == "symlink":
        note.symlink_to(tmp_path / "missing")
    else:
        os.mkfifo(note)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(OSError) as failure:
            okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    assert "a.md" in str(failure.value)
    assert kind in str(failure.value)


@pytest.mark.parametrize("denied_directory", [False, True])
def test_git_denied_entry_refusal_withholds_names(
    tmp_path: Path, denied_directory: bool
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    directory = tmp_path / "private-folder"
    directory.mkdir()
    note = directory / "secret.md"
    note.write_text("committed\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture")
    denied = directory if denied_directory else note
    denied.chmod(0)
    try:
        try:
            note.read_bytes()
        except PermissionError:
            pass
        else:
            pytest.skip("filesystem permits reading chmod-000 paths")
        vault = Vault(source_dir=tmp_path, settings=VaultSettings())
        try:
            with pytest.raises(ValueError) as failure:
                okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
        finally:
            vault.close()
        assert "private-folder" not in str(failure.value)
        assert "secret.md" not in str(failure.value)
    finally:
        denied.chmod(0o700 if denied_directory else 0o600)


@pytest.mark.parametrize("name", ["note.md", "image.png"])
@pytest.mark.parametrize("version", ["git-lfs.github.com", "hawser.github.com"])
@pytest.mark.parametrize("suffix", ["", "oid", "unknown", "long", "v1x"])
def test_git_lfs_version_prefix_without_pointer_fields_is_content(
    tmp_path: Path, name: str, version: str, suffix: str
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    first = f"version https://{version}/spec/v1"
    fields = "oid sha256:" + "a" * 64 + "\nsize 10\n"
    content = {
        "": first + "\n",
        "oid": first + "\noid sha256:" + "a" * 64 + "\n",
        "unknown": first[:-1] + "2\n" + fields,
        "long": first + "\n" + "ordinary prose " * 100,
        "v1x": first + "x\n" + fields,
    }[suffix].encode()
    (tmp_path / name).write_bytes(content)
    _git(tmp_path, "add", name)
    _git(tmp_path, "commit", "-qm", "fixture")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = okf_bundle.build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path))
    finally:
        vault.close()
    with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
        assert archive.read(name) == content
