"""Refuse writes into git submodules and plain nested repositories (#1564, #1949).

The superproject refuses to stage a path inside a submodule
(``docs/design/reference/git-submodules.md``), so a write there would land on
disk and never reach git. The existing write checks refuse it instead, naming the
protected repository.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.exceptions import SubmodulePathError
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.git.submodules import SubmoduleGuard
from markdown_vault_mcp.vault import Vault
from tests.fixtures.git import make_nested_repository, make_superproject

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_a_path_inside_the_submodule_is_found_on_the_first_call(
    tmp_path: Path,
) -> None:
    """The root comes from the vault, not from the first path asked about."""
    source = make_superproject(tmp_path)
    guard = SubmoduleGuard(source)
    assert guard.submodule_of(source / "sub" / "note.md") == ("sub", True)
    assert guard.submodule_of(source / "sub") == ("sub", True)
    assert guard.submodule_of(source / "sub" / "new" / "deeper.md") == ("sub", True)


def test_a_path_outside_every_submodule_is_none(tmp_path: Path) -> None:
    source = make_superproject(tmp_path)
    guard = SubmoduleGuard(source)
    assert guard.submodule_of(source / "top.md") is None
    assert guard.submodule_of(source / "subway.md") is None
    assert guard.submodule_of(source / "subway" / "x.md") is None


def test_a_folder_holding_a_submodule_is_found(tmp_path: Path) -> None:
    source = make_superproject(tmp_path, sub="deep/sub")
    guard = SubmoduleGuard(source)
    assert guard.submodule_of(source / "deep") == ("deep/sub", False)
    assert guard.submodule_of(source / "deep" / "sub" / "a.md") == ("deep/sub", True)


def test_a_vault_inside_the_repository_names_its_own_submodules(
    tmp_path: Path,
) -> None:
    """Gitlinks are relative to the repository; names are relative to the vault."""
    source = make_superproject(tmp_path, vault="notes")
    guard = SubmoduleGuard(source)
    assert guard.submodule_of(source / "sub" / "note.md") == ("sub", True)
    assert guard.submodule_of(source / "top.md") is None
    assert guard.submodule_of(source / "other" / "x.md") is None


def test_a_deinitialised_submodule_is_still_found(tmp_path: Path) -> None:
    source = make_superproject(tmp_path)
    _git(source, "submodule", "deinit", "--force", "--quiet", "sub")
    assert list((source / "sub").iterdir()) == []
    guard = SubmoduleGuard(source)
    assert guard.submodule_of(source / "sub" / "note.md") == ("sub", True)


def test_a_submodule_added_later_is_seen(tmp_path: Path) -> None:
    source = make_superproject(tmp_path)
    guard = SubmoduleGuard(source)
    assert guard.submodule_of(source / "later" / "x.md") is None
    _git(
        source,
        *("-c", "protocol.file.allow=always", "submodule", "add", "-q"),
        str(tmp_path / "upstream"),
        "later",
    )
    assert guard.submodule_of(source / "later" / "x.md") == ("later", True)


def test_a_vault_without_a_repository_refuses_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    guard = SubmoduleGuard(tmp_path)
    with caplog.at_level(logging.INFO, logger="markdown_vault_mcp.git.submodules"):
        assert guard.submodule_of(tmp_path / "sub" / "note.md") is None
        assert guard.submodule_of(tmp_path / "sub" / "note.md") is None
    assert [r.msg.split()[0] for r in caplog.records] == ["submodule_guard_inactive"]
    guard.refuse(tmp_path / "sub" / "note.md", "path", "sub/note.md")


def test_a_repository_with_no_index_yet_refuses_nothing(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    guard = SubmoduleGuard(tmp_path)
    assert guard.submodule_of(tmp_path / "sub" / "note.md") is None


def test_an_unchanged_index_is_not_listed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_superproject(tmp_path)
    guard = SubmoduleGuard(source)
    real = subprocess.run
    listings: list[None] = []

    def counting(args: list[str], **kw: Any) -> Any:
        if "ls-files" in args:
            listings.append(None)
        return real(args, **kw)

    monkeypatch.setattr("markdown_vault_mcp.git.submodules.subprocess.run", counting)
    for _ in range(3):
        guard.submodule_of(source / "sub" / "note.md")
    assert len(listings) == 1


def test_a_first_listing_that_fails_refuses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_superproject(tmp_path)
    _git(source, "submodule", "deinit", "--force", "--quiet", "sub")
    real = subprocess.run

    def failing(args: list[str], **kw: Any) -> Any:
        if "ls-files" in args:
            raise subprocess.CalledProcessError(128, args, stderr="fatal: broken")
        return real(args, **kw)

    monkeypatch.setattr("markdown_vault_mcp.git.submodules.subprocess.run", failing)
    guard = SubmoduleGuard(source)
    assert guard.submodule_of(source / "sub" / "note.md") is None


def test_a_failed_listing_keeps_the_last_one_and_warns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = make_superproject(tmp_path)
    guard = SubmoduleGuard(source)
    guard.submodule_of(source / "top.md")  # finds the root first
    real = subprocess.run

    def failing(args: list[str], **kw: Any) -> Any:
        if "ls-files" in args:
            raise subprocess.CalledProcessError(128, args, stderr="fatal: broken")
        return real(args, **kw)

    monkeypatch.setattr("markdown_vault_mcp.git.submodules.subprocess.run", failing)
    (source / "top.md").write_text("# Changed\n")
    _git(source, "add", "top.md")  # a new index, so it is read again
    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.git.submodules"):
        assert guard.submodule_of(source / "sub" / "note.md") == ("sub", True)
    assert [r.msg.split()[0] for r in caplog.records] == ["submodule_list_failed"]
    listings: list[None] = []

    def counting(args: list[str], **kw: Any) -> Any:
        if "ls-files" in args:
            listings.append(None)
        return real(args, **kw)

    monkeypatch.setattr("markdown_vault_mcp.git.submodules.subprocess.run", counting)
    # The failure was not cached: the unchanged index is listed again, once.
    assert guard.submodule_of(source / "sub" / "note.md") == ("sub", True)
    guard.submodule_of(source / "sub" / "note.md")
    assert len(listings) == 1


def test_an_index_git_will_not_name_is_listed_on_every_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = make_superproject(tmp_path)
    real = subprocess.run

    def no_git_dir(args: list[str], **kw: Any) -> Any:
        if "--absolute-git-dir" in args:
            raise subprocess.CalledProcessError(128, args)
        return real(args, **kw)

    monkeypatch.setattr("markdown_vault_mcp.git.submodules.subprocess.run", no_git_dir)
    guard = SubmoduleGuard(source)
    with caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.git.submodules"):
        assert guard.submodule_of(source / "sub" / "note.md") == ("sub", True)
    assert [r.msg.split()[0] for r in caplog.records] == ["submodule_index_unknown"]
    _git(
        source,
        *("-c", "protocol.file.allow=always", "submodule", "add", "-q"),
        str(tmp_path / "upstream"),
        "later",
    )
    assert guard.submodule_of(source / "later" / "x.md") == ("later", True)


def test_a_symlink_into_the_submodule_is_found(tmp_path: Path) -> None:
    source = make_superproject(tmp_path)
    (source / "alias").symlink_to(source / "sub")
    guard = SubmoduleGuard(source)
    assert guard.submodule_of(source / "alias" / "note.md") == ("sub", True)


def test_a_linked_worktree_vault_reads_its_own_index(tmp_path: Path) -> None:
    """``.git`` is a file there; git names the index."""
    source = make_superproject(tmp_path)
    worktree = tmp_path / "wt"
    _git(source, "worktree", "add", "-q", str(worktree))
    assert (worktree / ".git").is_file()
    guard = SubmoduleGuard(worktree)
    assert guard.submodule_of(worktree / "sub" / "note.md") == ("sub", True)


def test_a_vault_that_is_itself_a_submodule_has_none_of_its_own(
    tmp_path: Path,
) -> None:
    """Its git root is the submodule, which holds no gitlinks."""
    source = make_superproject(tmp_path)
    guard = SubmoduleGuard(source / "sub")
    assert guard.submodule_of(source / "sub" / "note.md") is None


def test_the_refusal_names_the_argument_and_the_submodule(tmp_path: Path) -> None:
    source = make_superproject(tmp_path, sub="deep/sub")
    guard = SubmoduleGuard(source)
    with pytest.raises(SubmodulePathError) as inside:
        guard.refuse(source / "deep" / "sub" / "x.md", "new_path", "deep/sub/x.md")
    assert str(inside.value) == (
        "new_path 'deep/sub/x.md' is inside the git submodule or nested repository 'deep/sub'; this "
        "server does not change files in these repositories, so the change would never "
        "be committed. Pass a new_path outside 'deep/sub/', or change the file "
        "in its own repository."
    )
    with pytest.raises(SubmodulePathError) as contains:
        guard.refuse(source / "deep", "old_dir", "deep")
    assert str(contains.value) == (
        "old_dir 'deep' holds the git submodule or nested repository 'deep/sub'; this server does not "
        "change files in these repositories, so the change would never be committed. "
        "Pass an old_dir that holds no submodule or nested repository."
    )


@pytest.mark.parametrize("gitfile", [False, True])
def test_plain_repositories_are_found_inside_and_below(
    tmp_path: Path, gitfile: bool
) -> None:
    source = make_nested_repository(tmp_path, vault="notes", sub="holder/nested")
    nested = source / "holder/nested"
    if gitfile:
        _git(nested, "init", "-q", f"--separate-git-dir={tmp_path / 'metadata'}")
        assert (nested / ".git").is_file()
    guard = SubmoduleGuard(source)
    assert guard.submodule_of(nested / "new/deep.md") == ("holder/nested", True)
    assert guard.submodule_of(nested) == ("holder/nested", True)
    assert guard.submodule_of(source / "holder") == ("holder/nested", False)
    assert guard.submodule_of(source / "holder/nested-neighbor/n.md") is None
    assert SubmoduleGuard(nested).submodule_of(nested / "note.md") is None
    (source / "alias").symlink_to(nested)
    assert guard.submodule_of(source / "alias/note.md") == ("holder/nested", True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX pathname spellings")
@pytest.mark.parametrize(
    "name", ["nested ", "nested\n", "nested\r", os.fsdecode(b"nested-\xff")]
)
@pytest.mark.parametrize("gitfile", [False, True])
def test_whitespace_and_non_utf8_boundaries_are_preserved(
    tmp_path: Path, name: str, gitfile: bool
) -> None:
    source = make_nested_repository(tmp_path, sub="holder/" + name)
    nested = source / "holder" / name
    if gitfile:
        _git(nested, "init", "-q", f"--separate-git-dir={tmp_path / 'metadata'}")
    guard = SubmoduleGuard(source)
    assert guard.submodule_of(nested / "new/deep.md") == ("holder/" + name, True)
    assert guard.submodule_of(source / "holder") == ("holder/" + name, False)


def test_inside_only_refusal_does_not_walk_descendants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_nested_repository(tmp_path, sub="b/sub")
    guard = SubmoduleGuard(source)

    def unexpected(*_args: Any, **_kw: Any) -> Any:
        pytest.fail("inside-only queries must not inspect unrelated descendants")

    monkeypatch.setattr("markdown_vault_mcp.git.submodules.os.walk", unexpected)
    guard.refuse(source / "b", "new_dir", "b", holding=False)


def test_inside_a_plain_repository_takes_precedence_over_holding_a_gitlink(
    tmp_path: Path,
) -> None:
    source = make_superproject(tmp_path, sub="outer/child")
    _git(source / "outer", "init", "-q")
    guard = SubmoduleGuard(source)
    assert guard.submodule_of(source / "outer") == ("outer", True)
    with pytest.raises(SubmodulePathError):
        guard.refuse(source / "outer", "new_dir", "outer", holding=False)


@pytest.mark.parametrize("inert", ["directory", "file"])
def test_inert_markers_do_not_hide_a_real_descendant(
    tmp_path: Path, inert: str
) -> None:
    source = make_nested_repository(tmp_path, sub="holder/nested")
    marker = source / "holder/.git"
    if inert == "directory":
        marker.mkdir()
    else:
        marker.write_text("not a gitfile\n")
    guard = SubmoduleGuard(source)
    assert guard.submodule_of(source / "holder") == ("holder/nested", False)
    assert guard.submodule_of(source / "holder/ordinary.md") is None


def test_markers_are_live_even_when_the_outer_index_is_unchanged(
    tmp_path: Path,
) -> None:
    source = make_nested_repository(tmp_path, sub="holder/nested")
    guard = SubmoduleGuard(source)
    nested = source / "holder/nested"
    marker = nested / ".git"
    hidden = tmp_path / "metadata"
    for enabled in [True, False, True]:
        expected = ("holder/nested", True) if enabled else None
        assert guard.submodule_of(nested / "new/n.md") == expected
        held = ("holder/nested", False) if enabled else None
        assert guard.submodule_of(source / "holder") == held
        if enabled:
            marker.rename(hidden)
        else:
            hidden.rename(marker)


def test_plain_repositories_are_found_without_an_outer_index(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    nested = tmp_path / "nested"
    nested.mkdir()
    _git(nested, "init", "-q")
    assert SubmoduleGuard(tmp_path).submodule_of(nested / "n.md") == ("nested", True)


def test_a_gitless_vault_stays_inactive_with_a_nested_repository(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    _git(nested, "init", "-q")
    assert SubmoduleGuard(tmp_path).submodule_of(nested / "n.md") is None


def test_marker_discovery_survives_a_failed_index_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_nested_repository(tmp_path)
    real = subprocess.run

    def failing(args: list[str], **kw: Any) -> Any:
        if "ls-files" in args:
            raise subprocess.CalledProcessError(128, args)
        return real(args, **kw)

    monkeypatch.setattr("markdown_vault_mcp.git.submodules.subprocess.run", failing)
    assert SubmoduleGuard(source).submodule_of(source / "sub/note.md") == ("sub", True)


def test_file_queries_do_not_walk_or_discover_git_without_a_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_nested_repository(tmp_path)
    guard = SubmoduleGuard(source)
    guard.submodule_of(source / "top.md")  # warm only the owning root/index

    def unexpected(*_args: Any, **_kw: Any) -> Any:
        pytest.fail("ordinary file queries must use only ancestor stats")

    monkeypatch.setattr("markdown_vault_mcp.git.submodules.os.walk", unexpected)
    monkeypatch.setattr("markdown_vault_mcp.git.submodules.subprocess.run", unexpected)
    assert guard.submodule_of(source / "top.md") is None
    assert guard.submodule_of(source / "ordinary/deep/n.md") is None


def test_holder_discovery_stops_before_entering_a_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_nested_repository(tmp_path, sub="holder/nested")
    (source / "holder/nested/ordinary").mkdir()
    real_walk = __import__("os").walk
    visited: list[Path] = []

    def walking(root: Path, **kw: Any) -> Any:
        for entry in real_walk(root, **kw):
            visited.append(Path(entry[0]))
            yield entry

    monkeypatch.setattr("markdown_vault_mcp.git.submodules.os.walk", walking)
    assert SubmoduleGuard(source).submodule_of(source / "holder") == (
        "holder/nested",
        False,
    )
    assert visited == [source / "holder", source / "holder/nested"]


def test_holder_walk_is_bounded_and_prunes_metadata_and_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_nested_repository(tmp_path)
    holder = source / "holder"
    (holder / ".git").mkdir(parents=True)  # inert metadata is still pruned
    (holder / "alias").symlink_to(source / "sub")
    real_walk = __import__("os").walk
    visited: list[Path] = []

    def walking(root: Path, **kw: Any) -> Any:
        assert root == holder
        for entry in real_walk(root, **kw):
            visited.append(Path(entry[0]))
            yield entry

    monkeypatch.setattr("markdown_vault_mcp.git.submodules.os.walk", walking)
    assert SubmoduleGuard(source).submodule_of(holder) is None
    assert visited == [holder]


@pytest.mark.parametrize("fault", ["stat", "walk"])
def test_denied_boundary_inspection_is_a_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    source = make_nested_repository(tmp_path)
    holder = source / "holder"
    holder.mkdir()
    guard = SubmoduleGuard(source)
    real_stat = Path.stat

    def denied_stat(path: Path, **kw: Any) -> Any:
        if path == holder / ".git":
            raise PermissionError(errno.EACCES, "denied", str(path))
        return real_stat(path, **kw)

    def denied_walk(root: Path, **kw: Any) -> Any:
        kw["onerror"](PermissionError(errno.EACCES, "denied", str(root)))
        return iter(())

    if fault == "stat":
        monkeypatch.setattr(Path, "stat", denied_stat)
    else:
        monkeypatch.setattr("markdown_vault_mcp.git.submodules.os.walk", denied_walk)
    with pytest.raises(PermissionError):
        guard.submodule_of(holder)


@example(parts=["0"])
@settings(max_examples=30, deadline=None)
@given(
    st.lists(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1),
        min_size=1,
    )
)
def test_generated_nested_descendants_and_prefix_neighbors(parts: list[str]) -> None:
    """Component containment, not a string prefix, defines the write boundary."""
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        components = ["p" + part[:24] for part in parts[:4]]
        sub = "/".join(components)
        source = make_nested_repository(root, sub=sub)
        guard = SubmoduleGuard(source)
        assert guard.submodule_of(source / sub / "new/deep.md") == (sub, True)
        assert guard.submodule_of(source / (sub + "-neighbor") / "n.md") is None


# ---------------------------------------------------------------------------
# The write tools
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_of(tmp_path: Path) -> Iterator[Callable[[Path], Vault]]:
    opened: list[tuple[Vault, GitWriteStrategy | None]] = []

    def build(source: Path, *, git: bool = True) -> Vault:
        strategy = (
            GitWriteStrategy(
                enable_pull=False, enable_push=False, git_lfs=False, repo_path=source
            )
            if git
            else None
        )
        vault = Vault(
            source_dir=source,
            settings=VaultSettings(index_path=tmp_path / "index.db", read_only=False),
            git_strategy=strategy,
            on_write=strategy,
        )
        opened.append((vault, strategy))
        vault.index.build_index()
        return vault

    yield build
    for vault, strategy in opened:
        vault.close()
        if strategy is not None:
            strategy.close()


def _snapshot(source: Path) -> dict[str, bytes]:
    return {
        p.relative_to(source).as_posix(): p.read_bytes()
        for p in sorted(source.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(source).parts
    }


@pytest.fixture(params=["plain", "redirected", "unsupported", "foreign-owned"])
def configured_nested_source(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[Path]:
    """Build staging boundaries without relying on nested worktree discovery."""
    source = make_nested_repository(tmp_path, sub="holder/nested")
    nested = source / "holder/nested"
    marker = nested / ".git"
    owners: list[tuple[Path, int]] = []
    try:
        if request.param == "redirected":
            alternate = tmp_path / "alternate"
            alternate.mkdir()
            _git(nested, "config", "core.worktree", str(alternate))
        elif request.param == "unsupported":
            _git(nested, "config", "core.repositoryformatversion", "1")
            _git(nested, "config", "extensions.futureFormat", "true")
        elif request.param == "foreign-owned":
            if os.name != "posix" or shutil.which("sudo") is None:
                pytest.skip("foreign ownership requires POSIX and noninteractive sudo")
            if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode:
                pytest.skip("noninteractive ownership changes unavailable")
            foreign = 0 if os.getuid() else 1
            for target in (nested, marker):
                owners.append((target, target.stat().st_uid))
                subprocess.run(
                    ["sudo", "-n", "chown", str(foreign), str(target)], check=True
                )
                assert target.stat().st_uid != os.getuid()
            assert (
                subprocess.run(
                    ["git", "rev-parse", "--resolve-git-dir", str(marker)],
                    capture_output=True,
                ).returncode
                == 0
            )
        yield source
    finally:
        for target, owner in reversed(owners):
            subprocess.run(["sudo", "-n", "chown", str(owner), str(target)], check=True)
            assert target.stat().st_uid == owner


def test_a_plain_repository_write_is_refused_before_any_side_effect(
    configured_nested_source: Path,
    vault_of: Callable[[Path], Vault],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful outer git-add can stage nothing; do not lose the write."""
    source = configured_nested_source
    path = "holder/nested/note.md"
    _git(source, "add", "--", path)
    assert (
        subprocess.check_output(
            ["git", "-C", str(source), "diff", "--cached", "--name-only"]
        )
        == b""
    )
    vault = vault_of(source)
    before = _snapshot(source)
    head = (source / ".git" / "refs" / "heads" / "main").read_bytes()
    index = (source / ".git" / "index").read_bytes()
    indexed = vault.reader.read(path)
    callbacks: list[object] = []
    real_fire = vault._doc_mgr._notifier.fire

    def record(*args: Any) -> None:
        callbacks.append(args)
        real_fire(*args)

    monkeypatch.setattr(vault._doc_mgr._notifier, "fire", record)
    with pytest.raises(SubmodulePathError, match="nested repository"):
        vault.writer.write(path, "# Lost write\n")
    assert _snapshot(source) == before
    assert (source / ".git" / "refs" / "heads" / "main").read_bytes() == head
    assert (source / ".git" / "index").read_bytes() == index
    assert vault.reader.read(path) == indexed
    assert not callbacks
    guard = SubmoduleGuard(source)
    assert guard.submodule_of(source / path) == ("holder/nested", True)
    assert guard.submodule_of(source / "holder") == ("holder/nested", False)
    vault.writer.write("outside.md", "# Committed\n")
    assert vault._write_callback.drain(timeout=5)
    assert (
        subprocess.check_output(["git", "-C", str(source), "show", "HEAD:outside.md"])
        == b"# Committed\n"
    )


@pytest.mark.parametrize("fault", ["read", "launch"])
@pytest.mark.parametrize("query", ["inside", "holding", "write"])
def test_candidate_inspection_fault_prevents_mutation(
    tmp_path: Path,
    vault_of: Callable[[Path], Vault],
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    query: str,
) -> None:
    source = make_nested_repository(tmp_path, sub="holder/nested")
    nested = source / "holder/nested"
    _git(nested, "init", "-q", f"--separate-git-dir={tmp_path / 'metadata'}")
    vault = vault_of(source)
    guard = vault._doc_mgr._submodule_guard
    assert guard is not None
    guard.submodule_of(source / "top.md")  # warm owning root/index before injection
    before = _snapshot(source)
    git_state = [
        (source / ".git" / name).read_bytes() for name in ("index", "refs/heads/main")
    ]
    indexed = vault.reader.read("holder/nested/note.md")
    real_read = Path.read_bytes
    real_run = subprocess.run

    def denied_read(path: Path) -> bytes:
        if path == nested / ".git":
            raise OSError(errno.EIO, "synthetic gitfile read failure", str(path))
        return real_read(path)

    def missing_git(*args: Any, **kw: Any) -> Any:
        if "--resolve-git-dir" in args[0]:
            raise FileNotFoundError(errno.ENOENT, "synthetic missing Git")
        return real_run(*args, **kw)

    monkeypatch.setattr(
        vault._doc_mgr._notifier, "fire", lambda *_a: pytest.fail("unexpected callback")
    )
    if fault == "read":
        monkeypatch.setattr(Path, "read_bytes", denied_read)
    else:
        monkeypatch.setattr(
            "markdown_vault_mcp.git.submodules.subprocess.run", missing_git
        )
    with pytest.raises(OSError):
        if query == "write":
            vault.writer.write("holder/nested/note.md", "# Must not change\n")
        else:
            target = nested / "note.md" if query == "inside" else source / "holder"
            guard.submodule_of(target)
    assert _snapshot(source) == before
    assert [
        (source / ".git" / name).read_bytes() for name in ("index", "refs/heads/main")
    ] == git_state
    assert vault.reader.read("holder/nested/note.md") == indexed


REFUSED: dict[str, tuple[Callable[[Vault], object], str]] = {
    "write a new note": (lambda v: v.writer.write("sub/new.md", "# New\n"), "path"),
    "overwrite a note": (lambda v: v.writer.write("sub/note.md", "# X\n"), "path"),
    "append": (lambda v: v.writer.append("sub/note.md", "more\n"), "path"),
    "edit": (
        lambda v: v.writer.edit("sub/note.md", "# Upstream", "# Changed"),
        "path",
    ),
    "delete a note": (lambda v: v.writer.delete("sub/note.md"), "path"),
    "rename out of it": (
        lambda v: v.writer.rename("sub/note.md", "moved.md"),
        "old_path",
    ),
    "rename into it": (lambda v: v.writer.rename("top.md", "sub/top.md"), "new_path"),
    "write an attachment": (
        lambda v: v.writer.write_attachment("sub/new.pdf", b"%PDF-1.4\n"),
        "path",
    ),
    "delete an attachment": (lambda v: v.writer.delete("sub/a.pdf"), "path"),
    "rename an attachment out": (
        lambda v: v.writer.rename("sub/a.pdf", "a.pdf"),
        "old_path",
    ),
    "rename an attachment in": (
        lambda v: v.writer.rename("b.pdf", "sub/b.pdf"),
        "new_path",
    ),
    "move the submodule": (lambda v: v.writer.move_folder("sub", "else"), "old_dir"),
    "write into a new folder in it": (
        lambda v: v.writer.write("sub/deeper/new.md", "# New\n"),
        "path",
    ),
    "move a folder into it": (
        lambda v: v.writer.move_folder("folder", "sub/folder"),
        "new_dir",
    ),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_every_write_tool_refuses_and_writes_nothing(
    case: str, tmp_path: Path, vault_of: Callable[[Path], Vault]
) -> None:
    source = make_superproject(tmp_path)
    (source / "sub" / "a.pdf").write_bytes(b"%PDF-1.4\n")
    (source / "b.pdf").write_bytes(b"%PDF-1.4\n")
    (source / "folder").mkdir()
    (source / "folder" / "x.md").write_text("# X\n")
    vault = vault_of(source)
    before = _snapshot(source)
    call, argument = REFUSED[case]
    with pytest.raises(
        SubmodulePathError, match=rf"^{argument} .*submodule or nested repository 'sub'"
    ):
        call(vault)
    assert _snapshot(source) == before
    assert not (source / "sub" / "deeper").exists()


def test_a_deinitialised_submodule_is_refused_too(
    tmp_path: Path, vault_of: Callable[[Path], Vault]
) -> None:
    source = make_superproject(tmp_path)
    _git(source, "submodule", "deinit", "--force", "--quiet", "sub")
    vault = vault_of(source)
    with pytest.raises(
        SubmodulePathError, match="submodule or nested repository 'sub'"
    ):
        vault.writer.write("sub/note.md", "# Lost\n")
    assert list((source / "sub").iterdir()) == []


def test_moving_a_folder_that_holds_a_submodule_is_refused(
    tmp_path: Path, vault_of: Callable[[Path], Vault]
) -> None:
    source = make_superproject(tmp_path, sub="deep/sub")
    (source / "deep" / "mine.md").write_text("# Mine\n")
    vault = vault_of(source)
    before = _snapshot(source)
    with pytest.raises(SubmodulePathError, match=r"^old_dir 'deep' holds"):
        vault.writer.move_folder("deep", "moved")
    assert _snapshot(source) == before


def test_a_folder_may_move_beside_a_submodule(
    tmp_path: Path, vault_of: Callable[[Path], Vault]
) -> None:
    """A destination that holds a submodule loses nothing moved next to it."""
    source = make_superproject(tmp_path, sub="b/sub")
    (source / "a").mkdir()
    (source / "a" / "x.md").write_text("# X\n")
    vault = vault_of(source)
    vault.writer.move_folder("a", "b")
    assert (source / "b" / "x.md").read_text() == "# X\n"


def test_move_destination_does_not_inspect_unrelated_private_subtree(
    tmp_path: Path, vault_of: Callable[[Path], Vault], monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_nested_repository(tmp_path)
    (source / "a").mkdir()
    (source / "a/x.md").write_text("# Move\n")
    private = source / "b/private"
    private.mkdir(parents=True)
    (private / "unrelated.txt").write_text("untouched\n")
    vault = vault_of(source)
    real_walk = os.walk

    def walking(root: Path, **kw: Any) -> Any:
        if root == source / "b":
            kw["onerror"](PermissionError(errno.EACCES, "denied", str(private)))
        yield from real_walk(root, **kw)

    monkeypatch.setattr("markdown_vault_mcp.git.submodules.os.walk", walking)
    vault.writer.move_folder("a", "b")
    assert not vault._doc_mgr.in_submodule("b")
    assert (source / "b/x.md").read_text() == "# Move\n"
    assert not (source / "a").exists()
    assert (private / "unrelated.txt").read_text() == "untouched\n"


def test_a_folder_move_that_lands_a_file_in_a_submodule_is_refused(
    tmp_path: Path, vault_of: Callable[[Path], Vault]
) -> None:
    source = make_superproject(tmp_path, sub="b/sub")
    (source / "a" / "sub").mkdir(parents=True)
    (source / "a" / "sub" / "x.md").write_text("# X\n")
    vault = vault_of(source)
    before = _snapshot(source)
    with pytest.raises(
        SubmodulePathError,
        match=r"^new_dir 'b' would move a file to 'b/sub/x.md', inside .*'b/sub'",
    ):
        vault.writer.move_folder("a", "b")
    assert _snapshot(source) == before


def test_a_write_outside_the_submodule_goes_ahead(
    tmp_path: Path, vault_of: Callable[[Path], Vault]
) -> None:
    source = make_superproject(tmp_path)
    vault = vault_of(source)
    vault.writer.write("subway.md", "# Fine\n")
    assert (source / "subway.md").read_text() == "# Fine\n"


def test_a_vault_without_a_git_strategy_writes_as_before(
    tmp_path: Path, vault_of: Callable[[Path], Vault]
) -> None:
    """With nothing to commit, the write is not lost, so nothing is refused."""
    source = make_superproject(tmp_path)
    vault = vault_of(source, git=False)
    vault.writer.write("sub/new.md", "# New\n")
    assert (source / "sub" / "new.md").read_text() == "# New\n"


def test_a_backlink_inside_the_submodule_is_reported_not_rewritten(
    tmp_path: Path,
    vault_of: Callable[[Path], Vault],
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = make_superproject(tmp_path, note="# Upstream\n\n[[folder/x]]\n")
    (source / "folder").mkdir()
    (source / "folder" / "x.md").write_text("# X\n")
    vault = vault_of(source)
    before = (source / "sub" / "note.md").read_bytes()
    with caplog.at_level(logging.INFO, logger="markdown_vault_mcp.managers.document"):
        result = vault.writer.move_folder("folder", "moved")
    assert result.failed_links == ["sub/note.md"]
    assert (source / "sub" / "note.md").read_bytes() == before
    assert (source / "moved" / "x.md").is_file()
    skipped = [r for r in caplog.records if r.msg.startswith("link_rewrite_source_")]
    assert [r.msg.split()[0] for r in skipped] == ["link_rewrite_source_skipped"]
    assert "reason=submodule" in skipped[0].msg


def test_a_rename_says_which_backlinks_it_left_alone(
    tmp_path: Path, vault_of: Callable[[Path], Vault]
) -> None:
    source = make_superproject(tmp_path, note="# Upstream\n\n[[top]]\n")
    vault = vault_of(source)
    before = (source / "sub" / "note.md").read_bytes()
    result = vault.writer.rename("top.md", "renamed.md", update_links=True)
    assert result.updated_links == 0
    assert result.hint is not None
    assert "'sub/note.md' still name the old path" in result.hint
    assert (source / "sub" / "note.md").read_bytes() == before


def test_backlink_inspection_fault_does_not_abort_a_completed_rename(
    tmp_path: Path,
    vault_of: Callable[[Path], Vault],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_nested_repository(tmp_path)
    (source / "sub/note.md").write_text("# Backlink\n\n[[top]]\n")
    _git(source / "sub", "init", "-q", f"--separate-git-dir={tmp_path / 'metadata'}")
    vault = vault_of(source)
    assert vault._fts.get_backlinks("top.md")
    backlink = (source / "sub/note.md").read_bytes()
    real_read = Path.read_bytes
    real_fire = vault._doc_mgr._notifier.fire_rename
    callbacks: list[object] = []

    def denied(path: Path) -> bytes:
        if path == source / "sub/.git":
            raise OSError(errno.EIO, "synthetic gitfile read failure", str(path))
        return real_read(path)

    def record(*args: Any) -> None:
        callbacks.append(args)
        real_fire(*args)

    monkeypatch.setattr(Path, "read_bytes", denied)
    monkeypatch.setattr(vault._doc_mgr._notifier, "fire_rename", record)
    result = vault.writer.rename("top.md", "renamed.md", update_links=True)
    assert result.updated_links == 0
    assert result.hint is None
    assert (source / "sub/note.md").read_bytes() == backlink
    assert not (source / "top.md").exists()
    assert (source / "renamed.md").read_text() == "# Top\n"
    assert vault.index.wait_for_drain(timeout=5)
    assert vault._fts.get_note("top.md") is None
    assert vault._fts.get_note("renamed.md") is not None
    assert len(callbacks) == 1
    assert vault._write_callback.drain(timeout=5)
    assert (
        subprocess.check_output(["git", "-C", str(source), "show", "HEAD:renamed.md"])
        == b"# Top\n"
    )
    assert (
        subprocess.check_output(
            ["git", "-C", str(source), "ls-tree", "--name-only", "HEAD", "top.md"]
        )
        == b""
    )


def test_a_rename_with_a_vanished_backlink_source_gives_no_submodule_hint(
    tmp_path: Path, vault_of: Callable[[Path], Vault]
) -> None:
    source = make_superproject(tmp_path)
    (source / "linker.md").write_text("# Linker\n\n[[top]]\n")
    vault = vault_of(source)
    (source / "linker.md").unlink()
    result = vault.writer.rename("top.md", "renamed.md", update_links=True)
    assert result.hint is None


def test_a_move_into_a_submodule_is_refused_before_a_collision(
    tmp_path: Path, vault_of: Callable[[Path], Vault]
) -> None:
    """The submodule, not the file in the way, is what the caller must change."""
    source = make_superproject(tmp_path, sub="b/sub")
    (source / "a" / "sub").mkdir(parents=True)
    (source / "a" / "sub" / "note.md").write_text("# Mine\n")
    vault = vault_of(source)
    with pytest.raises(SubmodulePathError, match=r"'b/sub/note\.md', inside"):
        vault.writer.move_folder("a", "b")


# ---------------------------------------------------------------------------
# The OKF tools write into folders they walk
# ---------------------------------------------------------------------------


def test_generating_the_root_index_leaves_the_submodule_out(
    tmp_path: Path, vault_of: Callable[[Path], Vault]
) -> None:
    source = make_superproject(tmp_path)
    (source / "folder").mkdir()
    (source / "folder" / "x.md").write_text("# X\n")
    vault = vault_of(source)
    result = vault.writer.okf_generate_index(folder="")
    assert result.created == ("folder/index.md",)
    assert not (source / "sub" / "index.md").exists()
    assert "/sub/index.md" not in (source / "index.md").read_text()


def test_a_submodule_with_its_own_index_keeps_its_pointer(
    tmp_path: Path, vault_of: Callable[[Path], Vault]
) -> None:
    source = make_superproject(tmp_path, extra={"index.md": "# Vendored\n"})
    vault = vault_of(source)
    before = (source / "sub" / "index.md").read_bytes()
    vault.writer.okf_generate_index(folder="")
    assert "/sub/index.md" in (source / "index.md").read_text()
    assert (source / "sub" / "index.md").read_bytes() == before


def test_indexing_a_folder_that_holds_a_submodule_skips_only_the_submodule(
    tmp_path: Path, vault_of: Callable[[Path], Vault]
) -> None:
    source = make_superproject(tmp_path, sub="deep/sub")
    (source / "deep" / "mine.md").write_text("# Mine\n")
    vault = vault_of(source)
    vault.writer.okf_generate_index(folder="deep")
    assert "/deep/mine.md" in (source / "deep" / "index.md").read_text()
    assert not (source / "deep" / "sub" / "index.md").exists()


def test_converting_links_leaves_submodule_notes_alone(
    tmp_path: Path,
    vault_of: Callable[[Path], Vault],
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = make_superproject(tmp_path, note="# Upstream\n\n[[top]]\n")
    (source / "mine.md").write_text("# Mine\n\n[[top]]\n")
    vault = vault_of(source)
    before = (source / "sub" / "note.md").read_bytes()
    with caplog.at_level(
        logging.INFO, logger="markdown_vault_mcp.managers.okf_migrate"
    ):
        result = vault.writer.okf_convert_links()
    skipped = [r for r in caplog.records if r.msg.startswith("okf_convert_links_")]
    assert [(r.msg.split()[0], r.args) for r in skipped] == [
        ("okf_convert_links_submodule_notes_skipped", (1,))
    ]
    assert result.files_changed == 1
    assert "[[top]]" not in (source / "mine.md").read_text()
    assert (source / "sub" / "note.md").read_bytes() == before


@pytest.mark.parametrize(
    "call",
    [
        lambda v: v.writer.okf_generate_index(folder="sub"),
        lambda v: v.writer.okf_convert_links(folder="sub"),
        lambda v: v.writer.okf_seed_log(folder="sub"),
    ],
    ids=["generate_index", "convert_links", "seed_log"],
)
def test_an_okf_folder_inside_the_submodule_is_refused(
    call: Callable[[Vault], object],
    tmp_path: Path,
    vault_of: Callable[[Path], Vault],
) -> None:
    source = make_superproject(tmp_path)
    vault = vault_of(source)
    before = _snapshot(source)
    with pytest.raises(SubmodulePathError, match=r"^folder 'sub' is inside"):
        call(vault)
    assert _snapshot(source) == before
