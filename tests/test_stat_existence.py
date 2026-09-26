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

from markdown_vault_mcp.exceptions import DocumentNotFoundError
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
    with pytest.raises(PermissionError) as exc:
        call(vault)
    assert not isinstance(exc.value, DocumentNotFoundError)


def test_unreadable_note_survives_a_reindex(vault: Vault, locked: Path) -> None:
    """A note that cannot be read right now is not a deleted note."""
    assert vault.reader.get_metadata("locked/n.md") is not None
    (locked / "locked").chmod(0)
    vault.index.reindex()
    assert vault.reader.get_metadata("locked/n.md") is not None
