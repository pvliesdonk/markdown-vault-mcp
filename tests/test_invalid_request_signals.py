"""Caller-caused refusals carry a signal of their own (#1608).

The design (docs/design/design.md § Error Handling) maps a library exception
to a tool outcome, and treats an untyped exception as a server fault. A
refusal the caller caused is therefore an ``InvalidRequestError``, and a
missing document its ``DocumentNotFoundError`` subclass. Both stay
``ValueError``s so existing ``except ValueError`` handlers keep working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.exceptions import (
    DocumentNotFoundError,
    InvalidRequestError,
    MarkdownMCPError,
)
from markdown_vault_mcp.okf import parse_stale_filter
from markdown_vault_mcp.utils import (
    resolve_inside,
    validate_history_dir,
    validate_history_path,
    validate_path,
)
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_writer_drain

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


def test_invalid_request_is_a_library_error_and_a_value_error() -> None:
    assert issubclass(InvalidRequestError, MarkdownMCPError)
    assert issubclass(InvalidRequestError, ValueError)


def test_document_not_found_is_an_invalid_request() -> None:
    assert issubclass(DocumentNotFoundError, InvalidRequestError)


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    root = tmp_path / "vault"
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "note.md").write_text("# Note\n\n## Part\n\nbody\n", encoding="utf-8")
    (root / "sub" / "a.md").write_text("# A\n\nsee [[note]]\n", encoding="utf-8")
    col = Vault(
        source_dir=root,
        settings=VaultSettings(read_only=False, max_note_read_bytes=10_000),
    )
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


# Each case: a call through the public facade that the caller got wrong.
_INVALID: dict[str, Callable[[Vault], Any]] = {
    "path escape": lambda v: v.writer.write("../x.md", "# X\n"),
    "note path without .md": lambda v: v.writer.write("x.txt", "# X\n"),
    "empty section": lambda v: v.reader.read("note.md", section=" "),
    "unknown heading": lambda v: v.reader.read("note.md", section="Nope"),
    "toc max_notes": lambda v: v.reader.get_toc("sub", max_notes=0),
    "toc max_level": lambda v: v.reader.get_toc("sub", max_level=0),
    "empty append": lambda v: v.writer.append("note.md", ""),
    "empty old_text": lambda v: v.writer.edit("note.md", old_text="", new_text="x"),
    "no edit target": lambda v: v.writer.edit("note.md"),
    "half a line range": lambda v: v.writer.edit("note.md", line_start=1),
    "line_start below 1": lambda v: v.writer.edit("note.md", line_start=0, line_end=1),
    "inverted line range": lambda v: v.writer.edit("note.md", line_start=3, line_end=1),
    "line_end past the file": lambda v: v.writer.edit(
        "note.md", line_start=1, line_end=999
    ),
    "move folder onto itself": lambda v: v.writer.move_folder("sub", "sub"),
    "move folder into itself": lambda v: v.writer.move_folder("sub", "sub/deep"),
    "folder scope is the root": lambda v: v.writer.move_folder("/", "x"),
    "chunks_per_file below 1": lambda v: v.reader.search(
        "body", mode="keyword", chunks_per_file=0
    ),
}


@pytest.mark.parametrize("call", _INVALID.values(), ids=_INVALID.keys())
def test_caller_mistake_raises_invalid_request(
    vault: Vault, call: Callable[[Vault], Any]
) -> None:
    with pytest.raises(InvalidRequestError):
        call(vault)


_MISSING: dict[str, Callable[[Vault], Any]] = {
    "section of a missing note": lambda v: v.reader.read("ghost.md", section="X"),
    "toc of a missing note": lambda v: v.reader.get_toc("ghost.md"),
    "backlinks": lambda v: v.graph.get_backlinks("ghost.md"),
    "outlinks": lambda v: v.graph.get_outlinks("ghost.md"),
    "connection path": lambda v: v.graph.get_connection_path("ghost.md", "note.md"),
    "similar": lambda v: v.reader.get_similar("ghost.md"),
    "context": lambda v: v.reader.get_context("ghost.md"),
}


@pytest.mark.parametrize("call", _MISSING.values(), ids=_MISSING.keys())
def test_missing_document_raises_document_not_found(
    vault: Vault, call: Callable[[Vault], Any]
) -> None:
    with pytest.raises(DocumentNotFoundError):
        call(vault)


def test_section_of_a_note_removed_since_indexing_is_not_found(vault: Vault) -> None:
    (vault.source_dir / "note.md").unlink()
    with pytest.raises(DocumentNotFoundError):
        vault.reader.read("note.md", section="Part")


def test_folder_scope_at_the_root_is_not_called_traversal(vault: Vault) -> None:
    """The vault root is a legal path, just not a folder scope."""
    with pytest.raises(InvalidRequestError, match="vault root") as exc:
        vault.writer.move_folder("sub/..", "x")
    assert "traversal" not in str(exc.value)


def test_oversized_read_is_an_invalid_request(vault: Vault) -> None:
    vault.writer.write("big.md", "# Big\n\n" + "word " * 5_000)
    wait_for_writer_drain(vault)
    with pytest.raises(InvalidRequestError, match="section="):
        vault.reader.read("big.md")


def test_path_helpers_raise_invalid_request(tmp_path: Path) -> None:
    with pytest.raises(InvalidRequestError):
        resolve_inside("../x", tmp_path)
    with pytest.raises(InvalidRequestError):
        validate_path("x.txt", tmp_path)
    with pytest.raises(InvalidRequestError):
        validate_history_path("x.exe", tmp_path, frozenset({"png"}))
    with pytest.raises(InvalidRequestError):
        validate_history_dir("/", tmp_path)


def test_stale_filter_value_is_an_invalid_request() -> None:
    with pytest.raises(InvalidRequestError):
        parse_stale_filter("maybe")
