"""An unreadable path is a fault, never absence (#1625).

On Python 3.14 ``Path.is_file()`` / ``is_dir()`` / ``exists()`` return
``False`` for a path the process cannot stat, where 3.12 raised. Deciding
existence with them turns a permission problem into "not found", and in the
index into a silent deletion.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.utils.fs import (
    could_be_regular_file,
    is_directory,
    is_regular_file,
    path_exists,
)
from markdown_vault_mcp.vault import Vault, VaultSettings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

pytestmark = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores permission bits",
)


@pytest.fixture
def locked(tmp_path: Path) -> Iterator[Path]:
    """A vault whose ``locked/`` folder cannot be searched."""
    root = tmp_path / "vault"
    (root / "locked").mkdir(parents=True)
    (root / "locked" / "n.md").write_text("# N\n\nbody\n", encoding="utf-8")
    (root / "open.md").write_text("# Open\n\nsee [[n]]\n", encoding="utf-8")
    yield root
    (root / "locked").chmod(0o755)


def test_helpers_raise_where_pathlib_answers_false(locked: Path) -> None:
    (locked / "locked").chmod(0)
    target = locked / "locked" / "n.md"
    for check in (is_regular_file, is_directory, path_exists):
        with pytest.raises(PermissionError):
            check(target)
    assert could_be_regular_file(target)
    assert not is_regular_file(locked / "ghost.md")
    assert not could_be_regular_file(locked / "ghost.md")
    assert is_directory(locked / "locked")


@pytest.fixture
def vault(locked: Path) -> Iterator[Vault]:
    col = Vault(source_dir=locked, settings=VaultSettings(read_only=False))
    try:
        col.index.build_index()
        yield col
    finally:
        (locked / "locked").chmod(0o755)
        col.close()


_TOOL_CALLS: dict[str, Callable[[Vault], Any]] = {
    "edit": lambda v: v.writer.edit("locked/n.md", old_text="body", new_text="x"),
    "append": lambda v: v.writer.append("locked/n.md", "x"),
    "delete": lambda v: v.writer.delete("locked/n.md"),
    "rename": lambda v: v.writer.rename("locked/n.md", "moved.md"),
    "write": lambda v: v.writer.write("locked/n.md", "# X\n"),
    "move folder": lambda v: v.writer.move_folder("locked", "elsewhere"),
}


@pytest.mark.parametrize("call", _TOOL_CALLS.values(), ids=_TOOL_CALLS.keys())
def test_unreadable_note_is_a_fault_not_absence(
    vault: Vault, locked: Path, call: Callable[[Vault], Any]
) -> None:
    (locked / "locked").chmod(0)
    with pytest.raises(PermissionError):
        call(vault)


def test_unreadable_note_survives_a_reindex(vault: Vault, locked: Path) -> None:
    """A note that cannot be read right now is not a deleted note."""
    assert vault.reader.get_metadata("locked/n.md") is not None
    (locked / "locked").chmod(0)
    vault.index.reindex()
    assert vault.reader.get_metadata("locked/n.md") is not None


def test_symlink_loop_reads_as_absent_like_pathlib(tmp_path: Path) -> None:
    """Only a refused stat changes meaning; a looping link stays "not there"."""
    loop = tmp_path / "loop.md"
    loop.symlink_to(loop)
    assert not is_regular_file(loop)
    assert not path_exists(loop)
    assert not could_be_regular_file(loop)


def test_move_folder_skips_a_looping_symlink(vault: Vault, locked: Path) -> None:
    (locked / "f").mkdir()
    (locked / "f" / "n.md").write_text("# N\n", encoding="utf-8")
    (locked / "f" / "loop.png").symlink_to(locked / "f" / "loop.png")
    vault.writer.move_folder("f", "g")
    assert (locked / "g" / "n.md").is_file()


@pytest.mark.parametrize("path", ["a\x00.md", "../../../outside.md"])
def test_history_validates_the_path_before_it_stats_it(
    tmp_path: Path, path: str
) -> None:
    from unittest.mock import MagicMock

    from markdown_vault_mcp.managers.git_query import GitQueryManager

    mgr = GitQueryManager(MagicMock(), tmp_path)
    with pytest.raises(InvalidRequestError):
        mgr.get_history(path)


def test_nested_unreadable_dir_keeps_only_what_is_under_it(tmp_path: Path) -> None:
    """a/locked is kept; a deleted a/open.md and a/lockedx/m.md are reported."""
    from markdown_vault_mcp.scanner import parse_note
    from markdown_vault_mcp.tracker import ChangeTracker

    root = tmp_path / "vault"
    rels = ("a/locked/n.md", "a/open.md", "a/lockedx/m.md")
    for rel in rels:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("# X\n", encoding="utf-8")
    tracker = ChangeTracker(tmp_path / "state.json")
    tracker.detect_changes(root)
    tracker.update_state([parse_note(root / rel, root) for rel in rels])
    (root / "a/open.md").unlink()
    (root / "a/lockedx/m.md").unlink()
    (root / "a/locked").chmod(0)
    try:
        changes = tracker.detect_changes(root)
    finally:
        (root / "a/locked").chmod(0o755)
    assert sorted(changes.deleted) == ["a/lockedx/m.md", "a/open.md"]


def test_dirty_path_refused_stat_keeps_the_row(vault: Vault, locked: Path) -> None:
    """The job fails for a retry; the note's row is not deleted as vanished."""
    (locked / "locked").chmod(0)
    with pytest.raises(OSError):
        vault._index_mgr.process_dirty_paths({"locked/n.md"})
    (locked / "locked").chmod(0o755)
    assert vault.reader.get_metadata("locked/n.md") is not None
