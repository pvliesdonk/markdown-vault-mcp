"""Custom title field wiring through document and vault operations."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.managers import document as document_module
from markdown_vault_mcp.managers.document import DocumentManager
from markdown_vault_mcp.scanner import HeadingChunker
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_writer_drain

if TYPE_CHECKING:
    from pathlib import Path


def test_manager_read_and_rename_use_custom_title_field(tmp_path: Path) -> None:
    (tmp_path / "original.md").write_text(
        "---\nname: Custom Title\ntitle: Other Title\n---\n# Heading Title\n",
        encoding="utf-8",
    )
    fts = FTSIndex(db_path=":memory:")
    try:
        manager = DocumentManager(
            fts=fts,
            source_dir=tmp_path,
            write_lock=threading.RLock(),
            chunk_strategy=HeadingChunker(),
            read_only=False,
            title_field="name",
        )
        note = manager.read("original.md")
        assert note is not None
        assert note.title == "Custom Title"
        with patch.object(
            document_module, "parse_note", wraps=document_module.parse_note
        ) as parse:
            manager.rename("original.md", "renamed.md")
        assert parse.call_count == 1
        assert parse.call_args.kwargs["title_field"] == "name"
    finally:
        fts.close()


@pytest.mark.parametrize(
    ("name_line", "expected"),
    [
        ("name: Custom Title\ntitle: Other Title\n", "Custom Title"),
        ("", "Heading Title"),
    ],
)
def test_vault_rename_refreshes_custom_title(
    tmp_path: Path, name_line: str, expected: str
) -> None:
    (tmp_path / "original.md").write_text(
        f"---\n{name_line}---\n# Heading Title\n\nbody\n",
        encoding="utf-8",
    )
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(read_only=False, title_field="name")
    )
    try:
        vault.index.build_index()
        vault.writer.rename("original.md", "renamed.md")
        wait_for_writer_drain(vault)
        note = vault.reader.read("renamed.md")
        assert note is not None
        assert note.title == expected
        results = vault.reader.search("body", mode="keyword")
        assert [(result.path, result.title) for result in results] == [
            ("renamed.md", expected)
        ]
    finally:
        vault.close()
