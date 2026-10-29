"""A write into a git submodule is refused before it touches disk (#1564).

The superproject refuses to stage a path inside a submodule
(``docs/design/reference/git-submodules.md``), so a write there would land on
disk and never reach git. Every write tool refuses it instead, naming the
submodule.
"""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.exceptions import SubmodulePathError
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.git.submodules import SubmoduleGuard
from markdown_vault_mcp.vault import Vault
from tests.fixtures.git import make_superproject

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


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
    monkeypatch.setattr("markdown_vault_mcp.git.submodules.subprocess.run", real)
    _git(
        source,
        *("-c", "protocol.file.allow=always", "submodule", "add", "-q"),
        str(tmp_path / "upstream"),
        "later",
    )
    # The failure was not cached: the next call lists the index again.
    assert guard.submodule_of(source / "later" / "x.md") == ("later", True)


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
        "new_path 'deep/sub/x.md' is inside the git submodule 'deep/sub'; this "
        "server does not change files in a submodule, so the change would never "
        "be committed. Pass a new_path outside 'deep/sub/', or change the file "
        "in the submodule's own repository."
    )
    with pytest.raises(SubmodulePathError) as contains:
        guard.refuse(source / "deep", "old_dir", "deep")
    assert str(contains.value) == (
        "old_dir 'deep' holds the git submodule 'deep/sub'; this server does not "
        "change files in a submodule, so the change would never be committed. "
        "Pass an old_dir that holds no submodule."
    )


# ---------------------------------------------------------------------------
# The write tools
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_of(tmp_path: Path) -> Iterator[Callable[[Path], Vault]]:
    opened: list[Vault] = []

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
        )
        opened.append(vault)
        vault.index.build_index()
        return vault

    yield build
    for vault in opened:
        vault.close()


def _snapshot(source: Path) -> dict[str, bytes]:
    return {
        p.relative_to(source).as_posix(): p.read_bytes()
        for p in sorted(source.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(source).parts
    }


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
    with pytest.raises(SubmodulePathError, match=rf"^{argument} .*submodule 'sub'"):
        call(vault)
    assert _snapshot(source) == before
    assert not (source / "sub" / "deeper").exists()


def test_a_deinitialised_submodule_is_refused_too(
    tmp_path: Path, vault_of: Callable[[Path], Vault]
) -> None:
    source = make_superproject(tmp_path)
    _git(source, "submodule", "deinit", "--force", "--quiet", "sub")
    vault = vault_of(source)
    with pytest.raises(SubmodulePathError, match="submodule 'sub'"):
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
    tmp_path: Path, vault_of: Callable[[Path], Vault]
) -> None:
    source = make_superproject(tmp_path, note="# Upstream\n\n[[top]]\n")
    (source / "mine.md").write_text("# Mine\n\n[[top]]\n")
    vault = vault_of(source)
    before = (source / "sub" / "note.md").read_bytes()
    result = vault.writer.okf_convert_links()
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
