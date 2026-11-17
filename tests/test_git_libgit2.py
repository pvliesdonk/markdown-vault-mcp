"""Real-repository checks for local libgit2 versioning."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.fixtures.libgit2 import _git
from tests.fixtures.libgit2 import isolated_repo as isolated_repo

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "key,value,shape",
    [
        ("commit.gpgsign", "true", "signed"),
        ("remote.origin.promisor", "true", "partial"),
        ("extensions.partialclone", "origin", "partial"),
    ],
)
def test_unsupported_config_is_refused(
    isolated_repo: Path, key: str, value: str, shape: str
) -> None:
    """Unsupported repository shapes fail before any commit is attempted."""
    from markdown_vault_mcp.exceptions import ConfigurationError
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    _git(isolated_repo, "config", key, value)
    with pytest.raises(ConfigurationError, match=shape):
        Libgit2Store(isolated_repo)


@pytest.mark.usefixtures("isolated_repo")
@pytest.mark.parametrize(
    "option,shape",
    [("--ref-format=reftable", "reftable"), ("--object-format=sha256", "SHA-256")],
)
def test_real_unsupported_repository_format_is_refused(
    tmp_path: Path, option: str, shape: str
) -> None:
    """Refuse actual repositories in unsupported formats."""
    import subprocess

    from markdown_vault_mcp.exceptions import ConfigurationError
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    repo = tmp_path / "unsupported"
    result = subprocess.run(
        ["git", "init", "-q", option, str(repo)], capture_output=True, check=False
    )
    if result.returncode:
        pytest.skip(f"Git does not support {option}")
    with pytest.raises(ConfigurationError, match=shape):
        Libgit2Store(repo)


def test_executable_commit_hook_refused(isolated_repo: Path) -> None:
    """Commit hooks cannot be silently bypassed."""
    from markdown_vault_mcp.exceptions import ConfigurationError
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    hook = isolated_repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(ConfigurationError, match="pre-commit"):
        Libgit2Store(isolated_repo)


def test_ignored_and_tracked_ignored_files(isolated_repo: Path) -> None:
    """Ignored untracked paths are skipped while tracked files remain writable."""
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    (isolated_repo / ".gitignore").write_text("*.md\n")
    store = Libgit2Store(isolated_repo)
    ignored = isolated_repo / "ignored.md"
    ignored.write_text("ignored")
    before = _git(isolated_repo, "rev-parse", "HEAD")
    store(ignored, "ignored", "write")
    assert _git(isolated_repo, "rev-parse", "HEAD") == before
    tracked = isolated_repo / "unrelated.md"
    tracked.write_text("new\n")
    store(tracked, "new\n", "edit")
    assert _git(isolated_repo, "show", "HEAD:unrelated.md") == "new\n"


def test_lifecycle_and_no_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-repository directory has no commits and closes repeatedly."""
    import os
    from pathlib import Path

    import pygit2

    from markdown_vault_mcp.git.interfaces import VersionedStore
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    discover = pygit2.discover_repository
    outer = discover(str(tmp_path))
    outer_gitdir = Path(outer) if outer else None
    outer_head = (outer_gitdir / "HEAD").read_bytes() if outer_gitdir else None
    outer_index = (outer_gitdir / "index").read_bytes() if outer_gitdir else None
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setattr(
        pygit2,
        "discover_repository",
        lambda path: discover(path, False, str(tmp_path.parent)),
    )
    store = Libgit2Store(tmp_path)
    assert isinstance(store, VersionedStore)
    note = tmp_path / "n.md"
    note.write_text("x")
    store(note, "x", "write")
    assert store.get_file_history(tmp_path, note, None, 10) == []
    store.stop()
    store.flush()
    store.close()
    store.close()
    if outer_gitdir:
        assert (outer_gitdir / "HEAD").read_bytes() == outer_head
        assert (outer_gitdir / "index").read_bytes() == outer_index


def test_local_force_pull_matches_cli(isolated_repo: Path) -> None:
    """Local-only pull returns the same structured outcome as CLI."""
    from markdown_vault_mcp.git.libgit2 import Libgit2Store
    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    cli = GitWriteStrategy(
        repo_path=isolated_repo, enable_pull=False, enable_push=False, git_lfs=False
    )
    native = Libgit2Store(isolated_repo)
    for dry_run in (False, True):
        assert native.force_pull(dry_run=dry_run) == cli.force_pull(dry_run=dry_run)


def test_eol_attributes_are_honored(isolated_repo: Path) -> None:
    """Normal Git text normalization remains available without external filters."""
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    (isolated_repo / ".gitattributes").write_text("*.md text eol=lf\n")
    note = isolated_repo / "note.md"
    note.write_bytes(b"one\r\ntwo\r\n")
    store = Libgit2Store(isolated_repo)
    store(note, "", "write")
    assert _git(isolated_repo, "show", "HEAD:note.md") == "one\ntwo\n"


def test_batch_preserves_principal_and_single_primary_subject(
    isolated_repo: Path,
) -> None:
    """Batch attribution and subject follow the same rules as the CLI store."""
    from markdown_vault_mcp._identity import Principal
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    store = Libgit2Store(
        isolated_repo, commit_name="Service", commit_email="service@example.com"
    )
    principal = Principal(
        subject="fixture",
        display_name="Person",
        email="person@example.com",
        kind="human",
    )
    first, second = isolated_repo / "one.md", isolated_repo / "two.md"
    first.write_text("one")
    second.write_text("two")
    store.on_write_batch(
        [(first, "write", None, principal), (second, "write", None, principal)],
        "write",
        primary_paths=frozenset({first}),
    )
    assert (
        _git(isolated_repo, "log", "-1", "--format=%s|%an|%ae|%cn|%ce").strip()
        == "write: one.md|Person|person@example.com|Service|service@example.com"
    )


def test_linked_worktree_is_supported(isolated_repo: Path, tmp_path: Path) -> None:
    """A linked worktree reads its shared Git configuration and index."""
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    other = tmp_path / "linked"
    _git(isolated_repo, "worktree", "add", "-qb", "other", str(other))
    note = other / "note.md"
    note.write_text("linked\n")
    Libgit2Store(other)(note, "linked\n", "write")
    assert _git(other, "show", "HEAD:note.md") == "linked\n"


def test_symlink_and_executable_modes(isolated_repo: Path) -> None:
    """A symlink is stored as a link and executable bits are retained."""
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    store = Libgit2Store(isolated_repo)
    link = isolated_repo / "link.md"
    link.symlink_to("target.md")
    store(link, "", "write")
    assert _git(isolated_repo, "ls-tree", "HEAD", "link.md").startswith("120000 ")
    script = isolated_repo / "script.md"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    store(script, "", "write")
    assert _git(isolated_repo, "ls-tree", "HEAD", "script.md").startswith("100755 ")


def test_missing_extra_has_actionable_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The optional dependency fails with install and CLI guidance."""
    import builtins

    from markdown_vault_mcp.exceptions import ConfigurationError
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    original = builtins.__import__

    def importing(name: str, *args: object, **kwargs: object) -> object:
        if name == "pygit2":
            raise ImportError("fixture")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    with pytest.raises(ConfigurationError, match=r"libgit2.*GitWriteStrategy"):
        Libgit2Store(tmp_path)


def test_native_modules_do_not_shell_out() -> None:
    """Native operations have no subprocess or Git runner dependency."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src/markdown_vault_mcp/git"
    for path in (root / "libgit2.py", root / "libgit2_history.py"):
        tree = ast.parse(path.read_text())
        imports = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ]
        imports += [
            name.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for name in node.names
        ]
        assert "subprocess" not in imports
        assert "markdown_vault_mcp.git._run" not in imports
        assert not any(
            isinstance(node, ast.ImportFrom)
            and node.module == "markdown_vault_mcp.git"
            and any(alias.name == "_run" for alias in node.names)
            for node in ast.walk(tree)
        )
