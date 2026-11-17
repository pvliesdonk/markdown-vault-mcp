"""Real-repository checks for local libgit2 versioning."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.fixtures.libgit2 import _git
from tests.fixtures.libgit2 import isolated_repo as isolated_repo

if TYPE_CHECKING:
    from pathlib import Path


def test_literal_write_preserves_unrelated_staged_file(isolated_repo: Path) -> None:
    """Only the literal note enters the commit, leaving other staged work intact."""
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    note = isolated_repo / "a*[b].md"
    note.write_text("content\n")
    store = Libgit2Store(
        repo_path=isolated_repo, commit_name="Author", commit_email="author@example.com"
    )
    store(note, "content\n", "write")
    assert _git(isolated_repo, "show", "HEAD:a*[b].md") == "content\n"
    assert _git(isolated_repo, "show", "HEAD:unrelated.md") == "original\n"
    assert _git(isolated_repo, "show", ":unrelated.md") == "staged\n"


def test_effective_external_clean_filter_is_refused(isolated_repo: Path) -> None:
    """Never store raw bytes where Git would apply an external clean filter."""
    from markdown_vault_mcp.exceptions import ConfigurationError
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    _git(isolated_repo, "config", "filter.upper.clean", "tr a-z A-Z")
    _git(isolated_repo, "config", "filter.upper.required", "true")
    (isolated_repo / ".gitattributes").write_text("*.md filter=upper\n")
    note = isolated_repo / "note.md"
    note.write_text("hello\n")
    before = _git(isolated_repo, "rev-parse", "HEAD")
    with pytest.raises(ConfigurationError, match=r"(?i)filter.*GitWriteStrategy"):
        store = Libgit2Store(
            repo_path=isolated_repo,
            commit_name="Author",
            commit_email="author@example.com",
        )
        store(note, "hello\n", "write")
    assert _git(isolated_repo, "rev-parse", "HEAD") == before


@pytest.mark.parametrize(
    "name", ["star*.md", "bracket[1].md", "line\n.md", "café.md", ":name.md"]
)
def test_literal_write_rename_delete(isolated_repo: Path, name: str) -> None:
    """A note's complete lifecycle commits only its exact literal paths."""
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    store = Libgit2Store(isolated_repo)
    note = isolated_repo / name
    target = isolated_repo / ("renamed-" + name)
    note.write_text("a\n")
    store(note, "a\n", "write")
    note.rename(target)
    store(target, "a\n", "rename", old_path=note)
    assert _git(isolated_repo, "show", "HEAD:" + target.name) == "a\n"
    target.unlink()
    store(target, "", "delete")
    assert (
        _git(isolated_repo, "ls-tree", "--name-only", "HEAD", "--", target.name) == ""
    )
    assert _git(isolated_repo, "show", ":unrelated.md") == "staged\n"


@pytest.mark.parametrize(
    "key,value,shape",
    [
        ("commit.gpgsign", "true", "signed"),
        ("remote.origin.promisor", "true", "partial"),
        ("extensions.partialclone", "origin", "partial"),
        ("extensions.refStorage", "reftable", "reftable"),
        ("extensions.objectFormat", "sha256", "SHA-256"),
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


def test_lifecycle_and_no_git(tmp_path: Path) -> None:
    """A non-repository directory has no commits and closes repeatedly."""
    from markdown_vault_mcp.git.interfaces import VersionedStore
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

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


def test_committed_lfs_attributes_refused_at_open(isolated_repo: Path) -> None:
    """An LFS rule in the committed tree prevents a store from opening."""
    from markdown_vault_mcp.exceptions import ConfigurationError
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    (isolated_repo / ".gitattributes").write_text("*.bin filter=lfs diff=lfs\n")
    (isolated_repo / "data.bin").write_bytes(b"fixture")
    _git(isolated_repo, "add", ".gitattributes", "data.bin")
    _git(isolated_repo, "commit", "-qm", "attributes")
    with pytest.raises(ConfigurationError, match=r"(?i)lfs"):
        Libgit2Store(isolated_repo)


def test_local_force_results_match_cli(isolated_repo: Path) -> None:
    """Local-only pull and push return the same structured outcomes as CLI."""
    from markdown_vault_mcp.git.libgit2 import Libgit2Store
    from markdown_vault_mcp.git.strategy import GitWriteStrategy

    cli = GitWriteStrategy(
        repo_path=isolated_repo, enable_pull=False, enable_push=False, git_lfs=False
    )
    native = Libgit2Store(isolated_repo)
    for dry_run in (False, True):
        assert native.force_pull(dry_run=dry_run) == cli.force_pull(dry_run=dry_run)
        assert native.force_push(dry_run=dry_run) == cli.force_push(dry_run=dry_run)


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


def test_linked_worktree_refuses_shared_signing_config(
    isolated_repo: Path, tmp_path: Path
) -> None:
    """A linked worktree cannot bypass the main repository's signing policy."""
    from markdown_vault_mcp.exceptions import ConfigurationError
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    other = tmp_path / "linked"
    _git(isolated_repo, "worktree", "add", "-qb", "other", str(other))
    _git(isolated_repo, "config", "commit.gpgsign", "true")
    with pytest.raises(ConfigurationError, match="signed"):
        Libgit2Store(other)


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


def test_effective_filter_added_after_open_is_refused(isolated_repo: Path) -> None:
    """A changed working-tree attribute cannot introduce a silent custom filter."""
    from markdown_vault_mcp.exceptions import ConfigurationError
    from markdown_vault_mcp.git.libgit2 import Libgit2Store

    store = Libgit2Store(isolated_repo)
    note = isolated_repo / "note.md"
    note.write_text("one")
    store(note, "one", "write")
    (isolated_repo / ".gitattributes").write_text("*.md filter=custom\n")
    note.write_text("two")
    with pytest.raises(ConfigurationError, match="filter"):
        store(note, "two", "edit")


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
