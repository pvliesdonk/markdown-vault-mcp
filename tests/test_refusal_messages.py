"""A refusal tells the model what was wrong and what to do next (#1639).

Library refusals reach the model word for word as tool errors. Each one names
the next step by tool, and none names a server setting the model cannot reach:
that goes in the log for the operator (the ``designing-tool-outcomes`` skill).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.exceptions import (
    DocumentExistsError,
    DocumentNotFoundError,
    InvalidRequestError,
)
from markdown_vault_mcp.vault import Vault, VaultSettings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    root = tmp_path / "vault"
    (root / "sub").mkdir(parents=True)
    (root / "note.md").write_text("# Note\n\n## Part\n\nbody\n", encoding="utf-8")
    (root / "big.md").write_text("# Big\n\n" + "word " * 400, encoding="utf-8")
    (root / "sub" / "a.md").write_text("# A\n\nsee [[note]]\n", encoding="utf-8")
    (root / "pic.png").write_bytes(b"\x89PNG")
    col = Vault(
        source_dir=root,
        settings=VaultSettings(
            read_only=False,
            write_protect_existing=True,
            attachment_extensions=["png"],
            max_note_read_bytes=500,
        ),
    )
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


# Each case: a refusal, and a word its next step must contain.
_REFUSALS: dict[str, tuple[Callable[[Vault], Any], str]] = {
    "edit missing": (
        lambda v: v.writer.edit("ghost.md", old_text="a", new_text="b"),
        "list_documents",
    ),
    "delete missing": (lambda v: v.writer.delete("ghost.md"), "list_documents"),
    "rename missing": (
        lambda v: v.writer.rename("ghost.md", "new.md"),
        "list_documents",
    ),
    "rename onto an existing note": (
        lambda v: v.writer.rename("note.md", "sub/a.md"),
        "new_path",
    ),
    "toc missing": (lambda v: v.reader.get_toc("ghost.md"), "list_documents"),
    "backlinks missing": (lambda v: v.graph.get_backlinks("ghost.md"), "search"),
    "similar missing": (lambda v: v.reader.get_similar("ghost.md"), "search"),
    "context missing": (lambda v: v.reader.get_context("ghost.md"), "search"),
    "connection path missing": (
        lambda v: v.graph.get_connection_path("ghost.md", "note.md"),
        "search",
    ),
    "section of a missing note": (
        lambda v: v.reader.read("ghost.md", section="Part"),
        "search",
    ),
    "attachment missing": (
        lambda v: v.reader.read_attachment("ghost.png"),
        "include_attachments",
    ),
    "folder missing": (
        lambda v: v.writer.move_folder("nowhere", "elsewhere"),
        "list_folders",
    ),
    "over the read cap": (lambda v: v.reader.read("big.md"), "section="),
    "write-protected note": (
        lambda v: v.writer.write("note.md", "# X\n"),
        "if_match",
    ),
    "write-protected attachment": (
        lambda v: v.writer.write_attachment("pic.png", b"x"),
        "if_match",
    ),
    "extension off the allowlist": (
        lambda v: v.reader.read_attachment("a.xyz"),
        "Allowed",
    ),
}


@pytest.mark.parametrize(
    ("call", "next_step"), _REFUSALS.values(), ids=_REFUSALS.keys()
)
def test_refusal_names_a_next_step_and_no_setting(
    vault: Vault, call: Callable[[Vault], Any], next_step: str
) -> None:
    with pytest.raises((InvalidRequestError, DocumentExistsError)) as exc:
        call(vault)
    assert next_step in str(exc.value)
    assert "MARKDOWN_VAULT_MCP" not in str(exc.value)


@pytest.mark.parametrize(
    ("call", "setting"),
    [
        (lambda v: v.reader.read("big.md"), "MAX_NOTE_READ_BYTES"),
        (lambda v: v.writer.write("note.md", "# X\n"), "WRITE_PROTECT_EXISTING"),
        (lambda v: v.reader.read_attachment("a.xyz"), "ATTACHMENT_EXTENSIONS"),
    ],
    ids=["read cap", "write protection", "allowlist"],
)
def test_the_operator_setting_is_logged(
    vault: Vault,
    caplog: pytest.LogCaptureFixture,
    call: Callable[[Vault], Any],
    setting: str,
) -> None:
    with (
        caplog.at_level(logging.INFO),
        pytest.raises((InvalidRequestError, DocumentExistsError)),
    ):
        call(vault)
    assert any(
        r.levelno == logging.INFO and f"MARKDOWN_VAULT_MCP_{setting}" in r.getMessage()
        for r in caplog.records
    )


def test_not_found_messages_keep_their_prefix() -> None:
    """Callers and tests match on the prefix; the next step is appended."""
    assert str(DocumentNotFoundError.note("a.md")).startswith(
        "Document not found: a.md"
    )
    assert str(DocumentNotFoundError.attachment("a.png")).startswith(
        "Attachment not found: a.png"
    )
    assert str(DocumentNotFoundError.folder("f")).startswith("Folder not found: f")
